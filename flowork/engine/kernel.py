"""执行内核：令牌状态机与路由推进（引擎核心，对节点 kind 零分支）。

流程：activate（建令牌→on_enter→完成判定）→ finish（on_exit→记事件→路由）→
route（行为给出出边→逐边推进）。节点语义全部由 NodeBehavior 提供；
内核只负责状态、计数、事件与守卫（死循环/乐观锁）。
令牌/任务记录面拆至 token_records.py / task_records.py（Mixin 组合，入口类不变）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from flowork.definitions.definition import ProcessDefinition, load_definition
from flowork.engine.advance_gate import AdvanceGateMixin
from flowork.engine.context import RuntimeNodeContext
from flowork.engine.declared_hooks import run_declared_hook
from flowork.engine.errors import (
    DeadLoopError,
    EngineExecutionError,
    NoParticipantError,
    ParticipantCountMismatchError,
    ParticipantLimitError,
    RejectTargetMissing,
    RouteError,
    attribute_node_error,
)
from flowork.engine.task_records import TaskRecordsMixin
from flowork.engine.token_records import TokenRecordsMixin
from flowork.events.domain import DomainEvent
from flowork.runtime.behavior import NodeBehavior
from flowork.runtime.lifecycle import UNSETTLED_INSTANCE_STATES, InstanceState, TokenState
from flowork.runtime.registry import HandlerRegistry, NodeRegistry

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.node import FlowNode
    from flowork.engine.config import EngineConfig
    from flowork.persistence.orm import ProcessInstanceRow, TokenRow

__all__ = [
    "DeadLoopError",
    "EngineExecutionError",
    "Kernel",
    "NoParticipantError",
    "ParticipantCountMismatchError",
    "ParticipantLimitError",
    "RejectTargetMissing",
    "RouteError",
    "attribute_node_error",
]


class Kernel(AdvanceGateMixin, TaskRecordsMixin, TokenRecordsMixin):
    """单命令内的执行内核（不提交事务；事务由 Command 管线管理）。"""

    def __init__(
        self,
        config: EngineConfig,
        registry: NodeRegistry,
        handlers: HandlerRegistry,
        events: list[tuple[str, dict[str, Any], str | None]],
        engine: Any = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.handlers = handlers
        self.engine = engine
        self.events = events  # 事件缓冲，由 HistoryInterceptor 统一落库
        self.command_ctx: Any = None  # CommandContext（实例跟踪，引擎装配时绑定）
        self._definition_cache: dict[str, ProcessDefinition] = {}

    # —— 定义 ——

    def definition_of(self, session: Session, instance: ProcessInstanceRow) -> ProcessDefinition:
        """实例的定义 = 发起时快照（模板后续演进不影响在途实例）。"""
        cached = self._definition_cache.get(instance.id)
        if cached is not None:
            return cached
        definition = load_definition(json.loads(instance.definition_snapshot), self.registry)
        self._definition_cache[instance.id] = definition
        return definition

    # —— 事件 ——

    def record_event(
        self, name: DomainEvent | str, payload: dict[str, Any], instance_id: str | None
    ) -> None:
        """记录领域事件（引擎内部一律传 DomainEvent 成员；端口对宿主开放事件名）。"""
        if self.command_ctx is not None and self.command_ctx.suppress_events:
            return  # 模拟实例（v2.3 A10）：事件不投 outbox（试跑不得惊动订阅方）
        self.events.append((name, payload, instance_id))
        if instance_id and self.command_ctx is not None:
            self.command_ctx.instances_touched.add(instance_id)
            # 变量留痕：钩子路径不经过 touch，此处补 before 快照（幂等）
            self.command_ctx.snapshot_variables(instance_id)

    # —— 实例 ——

    def complete_instance(
        self, session: Session, instance: ProcessInstanceRow, end_status: str
    ) -> None:
        if (
            instance.state == InstanceState.PAUSED.value
            and end_status == InstanceState.COMPLETED.value
        ):
            # 暂停不可直接「完成」（审计 D2：终态跃迁只从运行态走——清档请 resume
            # 或改用 cancel；cancel/terminate 不拦，级联砍树对暂停子照常结算）
            from flowork.errors import TaskNotAvailableError

            raise TaskNotAvailableError(
                "实例已暂停，不可直接完成——先 resume 或改用 cancel（spec §7 第二十轮）"
            )
        if instance.state in UNSETTLED_INSTANCE_STATES:
            instance.state = end_status
            instance.finished_at = self.config.now()
            session.flush()
            if end_status == InstanceState.COMPLETED.value:
                # 仅 completed 在此发事件（code-review 2026-10 P2-1）：cancelled/
                # terminated 由调用方独占（带 actor/reason/cascade 溯源）——
                # 此前终止路径双发假 cancelled（状态与事件序列矛盾）
                self.record_event(
                    DomainEvent.PROCESS_COMPLETED,
                    {"end_status": end_status},
                    instance.id,
                )
            from flowork.engine.subprocess_support import resume_parent_if_waiting

            resume_parent_if_waiting(self, session, instance)

    # —— 执行推进 ——

    def behavior_of(self, node: FlowNode) -> NodeBehavior:
        behavior_cls = self.registry.behavior_cls(node.kind)
        if behavior_cls is None:
            raise EngineExecutionError(f"节点类型 {node.kind!r} 未注册运行行为（Behavior）")
        return behavior_cls(node)

    def activate(
        self,
        session: Session,
        instance: ProcessInstanceRow,
        node: FlowNode,
        *,
        source_activity_code: str | None = None,
        reject_flag: bool = False,
        manual_participants: dict[str, list[str]] | None = None,
        defer_completion: bool = False,
    ) -> TokenRow:
        """激活节点：建令牌 → node_entered → on_enter → 完成判定。

        defer_completion=True 供 ``route`` 两段式扇出：先停靠（令牌+on_enter），
        完结推迟到兄弟臂令牌建齐后——否则即时完结臂会在兄弟令牌不存在时
        触发下游 all 汇聚放行（盲窗）。
        """
        try:
            token = self.create_token(
                session,
                instance,
                node.code,
                source_activity_code=source_activity_code,
                reject_flag=reject_flag,
                manual_participants=manual_participants,
            )
            self.record_event(
                DomainEvent.NODE_ENTERED,
                {"activity_code": node.code, "token_id": token.id},  # B4：并行分支可区分
                instance.id,
            )
            ctx = RuntimeNodeContext(self, session, instance, node, token)
            run_declared_hook(ctx, node.hooks.on_enter)  # 声明式先于行为：写变量供行为消费
            self.behavior_of(node).on_enter(ctx)
            if not defer_completion:
                self._maybe_complete(session, instance, node, token)
        except EngineExecutionError as error:  # 错误归因（复核批）：挂起可定位节点
            attribute_node_error(error, node.code)
            raise
        return token

    def _maybe_complete(
        self, session: Session, instance: ProcessInstanceRow, node: FlowNode, token: TokenRow
    ) -> None:
        if token.state != TokenState.UNFINISHED.value:
            return  # 节点已完成（如会签出口已达成）：重复提交不再推进
        ctx = RuntimeNodeContext(self, session, instance, node, token)
        if self.behavior_of(node).try_complete(ctx):
            self._finish_and_route(session, instance, node, token)

    def _finish_and_route(
        self, session: Session, instance: ProcessInstanceRow, node: FlowNode, token: TokenRow
    ) -> None:
        self.assert_not_dead_loop(session, instance, node)
        ctx = RuntimeNodeContext(self, session, instance, node, token)
        self.behavior_of(node).on_exit(ctx)
        run_declared_hook(ctx, node.hooks.on_exit)  # 行为语义完成后、令牌落定前
        self.finish_token(session, token, approval_exit=token.approval_exit)
        self.record_event(
            DomainEvent.NODE_EXITED,
            {"activity_code": node.code, "token_id": token.id},  # B4：并行分支可区分
            instance.id,
        )
        self.route(session, instance, node, token)

    def route(
        self,
        session: Session,
        instance: ProcessInstanceRow,
        node: FlowNode,
        token: TokenRow,
        *,
        behavior_override: NodeBehavior | None = None,
    ) -> None:
        """路由：行为给出出边列表 → on_take 钩子 → 激活目标节点。

        两段式（测试套件完善批 3 发现）：先全部停靠、再逐个判完结。逐边
        即时处理时，即时完结臂（cc/service）会在兄弟臂令牌创建前的瞬间
        触发下游 all 汇聚放行（盲窗）——汇聚双激活、多出的令牌无人收口。
        先建齐扇出令牌，``should_release`` 的可达性判定即健全。
        """
        definition = self.definition_of(session, instance)
        ctx = RuntimeNodeContext(self, session, instance, node, token)
        behavior = behavior_override or self.behavior_of(node)
        outgoing = behavior.get_outgoing(ctx)
        entered: list[tuple[FlowNode, TokenRow, bool]] = []
        for flow in outgoing:
            target = self._take_flow(session, instance, flow, token, definition)
            if target is not None:
                entered.append(target)
        for target_node, target_token, racing_cancel in entered:
            if racing_cancel:  # any 放行者的竞速收尾，完结前执行（时序见 _advance）
                self.cancel_racing_arms(session, instance, definition, target_node.code)
            self._maybe_complete(session, instance, target_node, target_token)

    def _node(self, definition: ProcessDefinition, code: str) -> FlowNode | None:
        return definition.node(code)

    # advance_after_task（任务提交后的统一推进，含出口不可达兜底）住
    # TaskRecordsMixin——任务推进域（v2.3 第二十轮迁出，kernel 守 300 行红线）。
    # 推进门（_take_flow/_advance/_advance_policy_of，any 吸收与竞速/all 放行）
    # 住 AdvanceGateMixin（engine/advance_gate.py，同因守红线迁出）。
