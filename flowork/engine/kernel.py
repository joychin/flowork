"""执行内核：令牌状态机与路由推进（引擎核心，对节点 kind 零分支）。

流程：activate（建令牌→on_enter→完成判定）→ finish（on_exit→记事件→路由）→
route（行为给出出边→逐边推进）。节点语义全部由 NodeBehavior 提供；
内核只负责状态、计数、事件与守卫（死循环/乐观锁）。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from flowork.definitions.builtin import UserTaskSpec
from flowork.definitions.definition import ProcessDefinition, load_definition
from flowork.engine.context import RuntimeNodeContext
from flowork.engine.join_gate import should_release
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
    from flowork.persistence.orm import ProcessInstanceRow, TaskRow, TokenRow

__all__ = ["DeadLoopError", "EngineExecutionError", "Kernel", "RouteError"]


class EngineExecutionError(FloworkError):
    """引擎执行错误（将转为 Incident，M5）。"""


class RouteError(EngineExecutionError):
    """路由无法确定（如条件分支条件全不满足且无默认边）。"""


class DeadLoopError(EngineExecutionError):
    """死循环守卫触发。"""


class NoParticipantError(EngineExecutionError):
    """审批节点无可用的参与者。"""


class RejectTargetMissing(EngineExecutionError):
    """驳回目标节点缺失（未配置且无来源节点）。"""


def _new_id() -> str:
    return uuid.uuid4().hex


class Kernel:
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
        self.events.append((name, payload, instance_id))
        if instance_id and self.command_ctx is not None:
            self.command_ctx.instances_touched.add(instance_id)

    # —— 令牌 ——

    def create_token(
        self,
        session: Session,
        instance: ProcessInstanceRow,
        activity_code: str,
        *,
        source_activity_code: str | None = None,
        reject_flag: bool = False,
        item_count: int = 0,
        manual_participants: dict[str, list[str]] | None = None,
    ) -> TokenRow:
        from flowork.persistence.orm import TokenRow as _TokenRow

        existing = self.tokens(session, instance.id)
        next_no = max((t.token_no for t in existing), default=0) + 1
        token = _TokenRow(
            id=_new_id(),
            instance_id=instance.id,
            token_no=next_no,
            activity_code=activity_code,
            source_activity_code=source_activity_code,
            state=TokenState.UNFINISHED.value,
            reject_flag=reject_flag,
            item_count=item_count,
            manual_participants=manual_participants or {},
        )
        session.add(token)
        session.flush()
        return token

    def tokens(
        self, session: Session, instance_id: str, states: tuple[str, ...] | None = None
    ) -> list[TokenRow]:
        from sqlalchemy import select

        from flowork.persistence.orm import TokenRow as _TokenRow

        stmt = (
            select(_TokenRow)
            .where(_TokenRow.instance_id == instance_id)
            .order_by(_TokenRow.token_no)
        )
        rows = list(session.scalars(stmt))
        if states:
            rows = [row for row in rows if row.state in states]
        return rows

    def touch_token(self, session: Session, token: TokenRow) -> None:
        session.flush()

    def finish_token(
        self, session: Session, token: TokenRow, *, approval_exit: str | None = None
    ) -> None:
        token.state = TokenState.FINISHED.value
        token.finished_at = self.config.now()
        if approval_exit:
            token.approval_exit = approval_exit
        session.flush()

    def cancel_token(self, session: Session, token: TokenRow) -> None:
        """取消令牌并撤销其未办任务（任务落历史 outcome=cancelled）。"""
        from flowork.persistence.orm import TaskHistoryRow

        for task in self.tasks(session, token.instance_id, token_id=token.id):
            session.add(
                TaskHistoryRow(
                    id=_new_id(),
                    task_id=task.id,
                    instance_id=task.instance_id,
                    token_id=task.token_id,
                    activity_code=task.activity_code,
                    name=task.name,
                    assignee=task.assignee,
                    task_type=task.task_type,
                    outcome="cancelled",
                    context_payload=task.context_payload,
                    acted_by="system",
                )
            )
            session.delete(task)
        token.state = TokenState.CANCELLED.value
        token.finished_at = self.config.now()
        session.flush()

    # —— 任务 ——

    def create_task(
        self,
        session: Session,
        instance: ProcessInstanceRow,
        token: TokenRow,
        node: FlowNode,
        *,
        assignee: str,
        name: str,
        task_type: str = "approval",
        context_payload: dict[str, Any] | None = None,
        root_task_id: str | None = None,
    ) -> TaskRow:
        from flowork.persistence.orm import TaskRow as _TaskRow

        task = _TaskRow(
            id=_new_id(),
            instance_id=instance.id,
            token_id=token.id,
            activity_code=node.code,
            name=name,
            assignee=assignee,
            task_type=task_type,
            context_payload=context_payload or {},
            root_task_id=root_task_id,
        )
        session.add(task)
        session.flush()
        self.record_event(
            DomainEvent.TASK_CREATED,
            {
                "task_id": task.id,
                "activity_code": node.code,
                "assignee": assignee,
                "task_type": task_type,
            },
            instance.id,
        )
        return task

    def tasks(
        self,
        session: Session,
        instance_id: str,
        *,
        token_id: str | None = None,
        assignee: str | None = None,
    ) -> list[TaskRow]:
        from sqlalchemy import select

        from flowork.persistence.orm import TaskRow as _TaskRow

        stmt = select(_TaskRow).where(_TaskRow.instance_id == instance_id)
        if token_id:
            stmt = stmt.where(_TaskRow.token_id == token_id)
        if assignee:
            stmt = stmt.where(_TaskRow.assignee == assignee)
        return list(session.scalars(stmt))

    MAX_ACTOR_CONTEXT_BYTES = 4096  # spec §5.6：JSON 袋上限，超限拒收

    def _actor_context_of(self, actor: str) -> dict[str, Any]:
        """身份快照：完成事务内经目录端口定格操作者当时的岗位/角色/部门。

        目录未配置（裸跑/单测）→ {}；内核不解析、不校验、不理解内容——
        存什么、存多细是宿主的审计策略（spec §5.6）。
        """
        import json as _json

        org = self.engine.config.org if self.engine else None
        if org is None:
            return {}
        try:
            snapshot = dict(org.snapshot(actor) or {})
        except Exception:
            return {}
        if (
            len(_json.dumps(snapshot, ensure_ascii=False).encode("utf-8"))
            > self.MAX_ACTOR_CONTEXT_BYTES
        ):
            raise EngineExecutionError(
                f"身份快照超限（>{self.MAX_ACTOR_CONTEXT_BYTES}B）：宿主 snapshot 返回内容过大"
            )
        return snapshot

    def archive_task(
        self,
        session: Session,
        task: TaskRow,
        *,
        outcome: str,
        comment: str = "",
        actor: str = "system",
        as_agent: bool = False,
        idempotency_key: str | None = None,
        cause_token_id: str | None = None,
    ) -> None:
        """任务归档：写历史（append-only）并从活表删除。"""
        import uuid as _uuid

        from flowork.persistence.orm import TaskHistoryRow

        cost = (
            (self.config.now() - task.created_at).total_seconds()
            if task.created_at is not None
            else 0.0
        )
        session.add(
            TaskHistoryRow(
                id=_uuid.uuid4().hex,
                task_id=task.id,
                instance_id=task.instance_id,
                token_id=task.token_id,
                activity_code=task.activity_code,
                name=task.name,
                assignee=task.assignee,
                task_type=task.task_type,
                outcome=outcome,
                comment=comment,
                context_payload=task.context_payload,
                acted_by=actor,
                acted_as_agent=as_agent,
                idempotency_key=idempotency_key,
                cause_token_id=cause_token_id,
                cost_seconds=cost,
                actor_context=self._actor_context_of(actor),
            )
        )
        session.delete(task)
        session.flush()

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

    # —— 参与者 / 已办 ——

    def resolve_participants(self, ctx: RuntimeNodeContext, spec: UserTaskSpec) -> Any:
        """参与者解析入口：返回 ParticipantResolution（todo + skipped）。"""
        from flowork.participants.resolver import (
            ParticipantResolution,
            fallback_when_empty,
            resolve_with_delegation,
        )

        org = self.engine.config.org if self.engine else None
        if org is not None:
            resolution, _delegation = resolve_with_delegation(
                ctx, spec, org, self.engine.config.participant_functions
            )
            if not resolution.todo and not resolution.skipped:
                return fallback_when_empty(ctx, spec, org)
            return resolution
        from flowork.participants.simple import resolve_simple

        return ParticipantResolution(todo=resolve_simple(ctx, spec))

    def handled_assignees(self, session: Session, token_id: str) -> list[str]:
        """令牌下已实际办理过的人（排除被取消的任务）。"""
        from sqlalchemy import select

        from flowork.persistence.orm import TaskHistoryRow

        stmt = select(TaskHistoryRow).where(TaskHistoryRow.token_id == token_id)
        return [row.assignee for row in session.scalars(stmt) if row.outcome != "cancelled"]

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
