"""任务记录与参与者解析面：建任务所需的一切（Kernel 组合件，入口类不变）。

从 kernel.py 拆出（单文件 ≤300 行回归）；Kernel 经 Mixin 组合获得本面。
``record_event`` 由 Kernel 提供，本类只留类型桩；
EngineExecutionError 住 kernel.py，函数内延迟导入避免模块环。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.definitions.builtin import UserTaskSpec
from flowork.engine.token_records import _new_id
from flowork.events.domain import DomainEvent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.node import FlowNode
    from flowork.engine.config import EngineConfig
    from flowork.engine.context import RuntimeNodeContext
    from flowork.persistence.orm import ProcessInstanceRow, TaskRow, TokenRow

__all__ = ["TaskRecordsMixin"]


class TaskRecordsMixin:
    """任务 CRUD、身份快照、归档与参与者解析；宿主 Kernel 提供 config/engine/record_event。"""

    config: EngineConfig
    engine: Any

    MAX_ACTOR_CONTEXT_BYTES = 4096  # spec §5.6：JSON 袋上限，超限拒收

    def record_event(self, name: str, payload: dict[str, Any], instance_id: str | None) -> None:
        """事件记录由 Kernel 提供。"""
        raise NotImplementedError

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
            from flowork.engine.kernel import EngineExecutionError

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
