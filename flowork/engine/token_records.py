"""令牌记录面：创建/查询/完结/取消（Kernel 组合件，入口类不变）。

从 kernel.py 拆出（单文件 ≤300 行回归）；Kernel 经 Mixin 组合获得本面。
任务查询 ``tasks`` 由 TaskRecordsMixin 提供，本类只留类型桩；
异常类住 kernel.py，需要时函数内延迟导入避免模块环。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from flowork.runtime.lifecycle import TokenState

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.definition import ProcessDefinition
    from flowork.definitions.node import FlowNode
    from flowork.engine.config import EngineConfig
    from flowork.engine.kernel import Kernel
    from flowork.persistence.orm import ProcessInstanceRow, TaskRow, TokenRow

__all__ = ["TokenRecordsMixin"]


def _new_id() -> str:
    return uuid.uuid4().hex


class TokenRecordsMixin:
    """令牌 CRUD 与取消；宿主 Kernel 提供 config 与任务查询。"""

    config: EngineConfig

    def record_event(self, name: str, payload: dict[str, Any], instance_id: str | None) -> None:
        """事件记录由 Kernel 提供（类型桩，mypy strict Mixin 契约）。"""
        raise NotImplementedError

    def assert_not_dead_loop(
        self, session: Session, instance: ProcessInstanceRow, node: FlowNode
    ) -> None:
        """死循环守卫（自 kernel 迁入守行数红线）：同节点完成次数达上限即拒。"""
        from flowork.engine.kernel import DeadLoopError

        finished_count = sum(
            1
            for row in self.tokens(session, instance.id)
            if row.activity_code == node.code and row.state == TokenState.FINISHED.value
        )
        if finished_count >= self.config.max_node_loops:
            raise DeadLoopError(
                f"节点 {node.code} 完成次数已达上限 {self.config.max_node_loops}，疑似死循环"
            )

    def tasks(
        self,
        session: Session,
        instance_id: str,
        *,
        token_id: str | None = None,
        assignee: str | None = None,
    ) -> list[TaskRow]:
        """任务查询由 Kernel（TaskRecordsMixin）提供。"""
        raise NotImplementedError

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

    def cancel_token(
        self, session: Session, token: TokenRow, *, cause: str = "", actor: str = "system"
    ) -> None:
        """取消令牌并撤销其未办任务（任务落历史 outcome=cancelled）。

        事件咽喉（2026-10-13 事件 review A4）：节点级 ``node_cancelled``
        （``by``=cause——撤回/终态级联/竞速/驳回/MI 收工/故障跳过/管理员七路
        统一）与逐任务 ``task_cancelled`` 都在此发，订阅方对「下游被撤回连带
        取消」不再失明。先经行为取消钩子（wait 关订阅删 Job，契约不变）。
        ``actor``（复核批：撤回/终止/驳回等有发起人的调用方穿透传入）——
        事件载荷与已办 ``acted_by`` 归因一致，不再一律记 system。
        """
        from flowork.events.domain import DomainEvent
        from flowork.persistence.orm import TaskHistoryRow

        self._notify_cancel(session, token)

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
                    acted_by=actor,
                )
            )
            session.delete(task)
            self.record_event(
                DomainEvent.TASK_CANCELLED,
                {
                    "task_id": task.id,
                    "activity_code": task.activity_code,
                    "task_type": task.task_type,
                    "assignee": task.assignee,
                    "actor": actor,
                },
                token.instance_id,
            )
        token.state = TokenState.CANCELLED.value
        token.finished_at = self.config.now()
        session.flush()
        self.record_event(
            DomainEvent.NODE_CANCELLED,
            {
                "activity_code": token.activity_code,
                "by": cause,
                "token_id": token.id,
                "actor": actor,  # 两级语义：状态层也带归因（复核批）
            },
            token.instance_id,
        )

    def cancel_racing_arms(
        self,
        session: Session,
        instance: ProcessInstanceRow,
        definition: ProcessDefinition,
        code: str,
    ) -> None:
        """任一到达即开始后取消竞速臂（判据＝``graph.racing_basin``，语义详彼处）。

        v2.3 第二十轮：不限一跳（长臂漏取消会让完结节点被迟到到达二次激活
        重跑）、也不越别的汇合域；直接前驱恒计入（与旧语义一致）。
        """
        from flowork.definitions.graph import racing_basin

        racing = racing_basin(definition, code) - {code}
        for token in self.tokens(session, instance.id, states=(TokenState.UNFINISHED.value,)):
            if token.activity_code in racing:
                self.cancel_token(session, token, cause="racing")

    def _notify_cancel(self, session: Session, token: TokenRow) -> None:
        """行为取消钩子（registry 中介，无行为/节点缺失时静默跳过）。"""
        from typing import cast

        from flowork.engine.context import RuntimeNodeContext
        from flowork.engine.declared_hooks import run_declared_hook
        from flowork.persistence.orm import ProcessInstanceRow

        kernel = cast("Kernel", self)
        instance = session.get(ProcessInstanceRow, token.instance_id)
        if instance is None:
            return
        node = kernel.definition_of(session, instance).node(token.activity_code)
        if node is None or kernel.registry.behavior_cls(node.kind) is None:
            return
        hook_ctx = RuntimeNodeContext(kernel, session, instance, node, token)
        run_declared_hook(hook_ctx, node.hooks.on_cancel)  # 声明式先于行为（declared_hooks）
        kernel.behavior_of(node).on_cancel(hook_ctx)
