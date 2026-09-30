"""HistoryService：只读查询（事件 / 已办 / 实例 / 令牌）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from flowork.engine.ops import CommandContext
from flowork.persistence.orm import (
    EventOutboxRow,
    ProcessInstanceRow,
    TaskHistoryRow,
    TokenRow,
)
from flowork.tasks.models import EventInfo, InstanceInfo, TaskHistoryInfo, TokenInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["HistoryService"]


class HistoryService:
    """历史与只读查询服务（待办查询在 TaskService.list_tasks）。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine

    def events(self, instance_id: str | None = None, *, limit: int = 200) -> list[EventInfo]:
        def _command(ctx: CommandContext) -> Any:
            stmt = select(EventOutboxRow).order_by(EventOutboxRow.occurred_at)
            if instance_id:
                stmt = stmt.where(EventOutboxRow.instance_id == instance_id)
            return [
                EventInfo(
                    id=row.id,
                    event_name=row.event_name,
                    instance_id=row.instance_id,
                    payload=row.payload,
                    occurred_at=row.occurred_at,
                )
                for row in ctx.session.scalars(stmt.limit(limit))
            ]

        result: list[EventInfo] = self._engine.execute(_command)
        return result

    def task_history(
        self,
        instance_id: str | None = None,
        assignee: str | None = None,
        *,
        limit: int = 200,
    ) -> list[TaskHistoryInfo]:
        def _command(ctx: CommandContext) -> Any:
            stmt = select(TaskHistoryRow).order_by(TaskHistoryRow.created_at)
            if instance_id:
                stmt = stmt.where(TaskHistoryRow.instance_id == instance_id)
            if assignee:
                stmt = stmt.where(TaskHistoryRow.assignee == assignee)
            return [_history(row) for row in ctx.session.scalars(stmt.limit(limit))]

        result: list[TaskHistoryInfo] = self._engine.execute(_command)
        return result

    def instances(
        self, *, state: str | None = None, definition_code: str | None = None, limit: int = 100
    ) -> list[InstanceInfo]:
        def _command(ctx: CommandContext) -> Any:
            stmt = select(ProcessInstanceRow).order_by(ProcessInstanceRow.started_at.desc())
            if state:
                stmt = stmt.where(ProcessInstanceRow.state == state)
            if definition_code:
                stmt = stmt.where(ProcessInstanceRow.definition_code == definition_code)
            return [
                InstanceInfo(
                    id=row.id,
                    definition_code=row.definition_code,
                    definition_version=row.definition_version,
                    business_key=row.business_key,
                    state=row.state,
                    start_user=row.start_user,
                    started_at=row.started_at,
                    finished_at=row.finished_at,
                    parent_id=row.parent_id,
                )
                for row in ctx.session.scalars(stmt.limit(limit))
            ]

        result: list[InstanceInfo] = self._engine.execute(_command)
        return result

    def tokens(self, instance_id: str) -> list[TokenInfo]:
        def _command(ctx: CommandContext) -> Any:
            stmt = (
                select(TokenRow)
                .where(TokenRow.instance_id == instance_id)
                .order_by(TokenRow.token_no)
            )
            return [
                TokenInfo(
                    id=row.id,
                    token_no=row.token_no,
                    activity_code=row.activity_code,
                    source_activity_code=row.source_activity_code,
                    state=row.state,
                    item_count=row.item_count,
                    approve_count=row.approve_count,
                    disapprove_count=row.disapprove_count,
                    reject_flag=row.reject_flag,
                    approval_exit=row.approval_exit,
                    created_at=row.created_at,
                    finished_at=row.finished_at,
                )
                for row in ctx.session.scalars(stmt)
            ]

        result: list[TokenInfo] = self._engine.execute(_command)
        return result


def _history(row: TaskHistoryRow) -> TaskHistoryInfo:
    return TaskHistoryInfo(
        id=row.id,
        task_id=row.task_id,
        instance_id=row.instance_id,
        token_id=row.token_id,
        activity_code=row.activity_code,
        name=row.name,
        assignee=row.assignee,
        task_type=row.task_type,
        state="completed",
        context_payload=row.context_payload,
        created_at=row.created_at,
        outcome=row.outcome,
        comment=row.comment,
        acted_by=row.acted_by,
        acted_as_agent=row.acted_as_agent,
        cause_token_id=row.cause_token_id,
    )
