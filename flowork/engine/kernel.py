"""执行内核：令牌状态机与路由推进（引擎核心，对节点 kind 零分支）。

流程：activate（建令牌→on_enter→完成判定）→ finish（on_exit→记事件→路由）→
route（行为给出出边→逐边推进）。节点语义全部由 NodeBehavior 提供；
内核只负责状态、计数、事件与守卫（死循环/乐观锁）。
令牌/任务记录面拆至 token_records.py / task_records.py（Mixin 组合，入口类不变）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.definitions.definition import ProcessDefinition, load_definition
from flowork.engine.context import RuntimeNodeContext
from flowork.engine.join_gate import should_release
from flowork.engine.task_records import TaskRecordsMixin
from flowork.engine.token_records import TokenRecordsMixin
from flowork.errors import FloworkError
from flowork.events.domain import DomainEvent
from flowork.runtime.behavior import NodeBehavior
from flowork.runtime.lifecycle import TokenState
from flowork.runtime.registry import HandlerRegistry, NodeRegistry

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.flow import SequenceFlow
    from flowork.definitions.node import FlowNode
    from flowork.engine.config import EngineConfig
    from flowork.persistence.orm import ProcessInstanceRow, TokenRow

__all__ = ["DeadLoopError", "EngineExecutionError", "Kernel", "RouteError"]


class EngineExecutionError(FloworkError):
    """引擎执行错误（将转为 Incident，M5）。"""


class RouteError(EngineExecutionError):
    """路由无法确定（如条件分支条件全不满足且无默认边）。"""


class DeadLoopError(EngineExecutionError):
    """死循环守卫触发。"""


class NoParticipantError(EngineExecutionError):
    """审批节点无可用的参与者。"""


class ParticipantLimitError(EngineExecutionError):
    """参与者规模超过上限（EngineConfig.max_participants）——拒绝而非截断。

    解析位（user_task/cc 进入）触发时按技术故障处理：挂起+故障单（可见、可修、
    可 retry）；调用方位（加签）由 TaskService 拦截为请求拒绝，实例不受影响。
    """


class ParticipantCountMismatchError(EngineExecutionError):
    """单人审批节点（未声明 multi_instance）解析出多名参与者——拒绝宽松并行。

    spec v2.3 第十八轮：多人必须显式声明会签；解析位＝技术故障（挂起+故障单，首节点整笔回滚）。
    """


class RejectTargetMissing(EngineExecutionError):
    """驳回目标节点缺失（未配置且无来源节点）。"""


class Kernel(TaskRecordsMixin, TokenRecordsMixin):
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
        import json

        definition = load_definition(json.loads(instance.definition_snapshot), self.registry)
        self._definition_cache[instance.id] = definition
        return definition

    # —— 事件 ——

    def record_event(self, name: str, payload: dict[str, Any], instance_id: str | None) -> None:
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
        if instance.state in ("running", "suspended", "paused"):
            instance.state = end_status
            instance.finished_at = self.config.now()
            session.flush()
            self.record_event(
                DomainEvent.PROCESS_COMPLETED
                if end_status == "completed"
                else DomainEvent.PROCESS_CANCELLED,
                {"end_status": end_status},
                instance.id,
            )
            from flowork.engine.subprocess_support import resume_parent_if_waiting

            resume_parent_if_waiting(self, session, instance)

    def normal_flows(self, ctx: RuntimeNodeContext) -> list[SequenceFlow]:
        """无条件出边（default 视同无条件）；条件路由见 engine.routing.select_outgoing。"""
        definition = self.definition_of(ctx.session, ctx.instance)
        return [flow for flow in definition.outgoing(ctx.node.code) if flow.condition is None]

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
    ) -> TokenRow:
        """激活节点：建令牌 → node_entered → on_enter → 完成判定。"""
        token = self.create_token(
            session,
            instance,
            node.code,
            source_activity_code=source_activity_code,
            reject_flag=reject_flag,
            manual_participants=manual_participants,
        )
        self.record_event(DomainEvent.NODE_ENTERED, {"activity_code": node.code}, instance.id)
        ctx = RuntimeNodeContext(self, session, instance, node, token)
        self.behavior_of(node).on_enter(ctx)
        self._maybe_complete(session, instance, node, token)
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
        # 死循环守卫
        finished_count = sum(
            1
            for row in self.tokens(session, instance.id)
            if row.activity_code == node.code and row.state == TokenState.FINISHED.value
        )
        if finished_count >= self.config.max_node_loops:
            raise DeadLoopError(
                f"节点 {node.code} 完成次数已达上限 {self.config.max_node_loops}，疑似死循环"
            )
        ctx = RuntimeNodeContext(self, session, instance, node, token)
        self.behavior_of(node).on_exit(ctx)
        self.finish_token(session, token, approval_exit=token.approval_exit)
        self.record_event(DomainEvent.NODE_EXITED, {"activity_code": node.code}, instance.id)
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
        """路由：行为给出出边列表 → on_take 钩子 → 激活目标节点。"""
        definition = self.definition_of(session, instance)
        ctx = RuntimeNodeContext(self, session, instance, node, token)
        behavior = behavior_override or self.behavior_of(node)
        outgoing = behavior.get_outgoing(ctx)
        for flow in outgoing:
            self._take_flow(session, instance, flow, token, definition)

    def _take_flow(
        self,
        session: Session,
        instance: ProcessInstanceRow,
        flow: SequenceFlow,
        from_token: TokenRow,
        definition: ProcessDefinition,
    ) -> None:
        if flow.on_take is not None:
            handler = self.handlers.resolve(flow.on_take.handler)
            source_node = self._node(definition, flow.source)
            assert source_node is not None
            hook_ctx = RuntimeNodeContext(self, session, instance, source_node, from_token)
            handler(hook_ctx, flow.on_take.params)
        target = self._node(definition, flow.target)
        if target is None:
            raise RouteError(f"目标节点不存在：{flow.target}")
        self._advance(session, instance, target, flow, definition)

    # —— 推进门（对齐 对标产品 checkCanBeActivited + handleActiviteActivity）——

    def _advance(
        self,
        session: Session,
        instance: ProcessInstanceRow,
        node: FlowNode,
        flow: SequenceFlow,
        definition: ProcessDefinition,
    ) -> None:
        """边到达后的推进判定：any 任一到达即办（缺省）/ all 可达性放行（§2.3）。

        - all：除已停靠本节点的令牌外无任何**未完令牌**可结构可达本节点才放行
          （条件未命中的臂落定后不再阻塞；旧 settled 令牌不算数，见 join_gate）；
        - any 且节点已在办：吸收本次到达，不重复激活；
        - any 放行后：取消仍未完成的兄弟分支（竞速收尾，防迟到到达）。
        驳回/撤回/管理员等直调 ``activate`` 的路径不经此门。
        """
        sources = [f.source for f in definition.incoming(node.code)]
        if len(sources) > 1:
            if self._advance_policy_of(node) == "all":
                unfinished_elsewhere = {
                    token.activity_code
                    for token in self.tokens(
                        session, instance.id, states=(TokenState.UNFINISHED.value,)
                    )
                    if token.activity_code != node.code
                }
                if not should_release(definition, unfinished_elsewhere, node.code):
                    return  # 仍有未完令牌可达，等待其余入边落定
            else:
                busy = any(
                    token.activity_code == node.code
                    for token in self.tokens(
                        session, instance.id, states=(TokenState.UNFINISHED.value,)
                    )
                )
                if busy:
                    return  # 吸收重复到达（节点已在办）
                self._cancel_sibling_branches(session, instance, sources)
        self.activate(session, instance, node, source_activity_code=flow.source)

    def _advance_policy_of(self, node: FlowNode) -> str:
        """节点开始条件（Spec 声明；cc_task 在其 Spec 上覆写恒 any）。缺省 any。"""
        return getattr(node, "advance_policy", None) or "any"

    def _cancel_sibling_branches(
        self, session: Session, instance: ProcessInstanceRow, sources: list[str]
    ) -> None:
        """任一到达即开始后，取消仍未完成的兄弟分支（避免僵尸分支与迟到到达）。"""
        source_set = set(sources)
        for token in self.tokens(session, instance.id, states=(TokenState.UNFINISHED.value,)):
            if token.activity_code in source_set:
                self.cancel_token(session, token)

    def _node(self, definition: ProcessDefinition, code: str) -> FlowNode | None:
        return definition.node(code)

    def advance_after_task(
        self, session: Session, instance: ProcessInstanceRow, token: TokenRow
    ) -> None:
        """任务提交后的统一推进入口。"""
        node = self.definition_of(session, instance).node(token.activity_code)
        if node is None:
            raise EngineExecutionError(f"令牌节点不存在：{token.activity_code}")
        self._maybe_complete(session, instance, node, token)
