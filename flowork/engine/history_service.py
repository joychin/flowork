"""HistoryService：只读查询（事件 / 已办 / 实例 / 令牌）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError
from flowork.persistence.orm import (
    EventOutboxRow,
    ProcessInstanceRow,
    TaskHistoryRow,
    TokenRow,
)
from flowork.tasks.models import EventInfo, InstanceInfo, TaskHistoryInfo, TokenInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["HistoryService", "Page", "TraceEntry"]


from typing import TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class Page(BaseModel):
    """分页结果。"""

    model_config = ConfigDict(extra="forbid")

    items: list[Any]
    total: int
    offset: int = 0
    limit: int = 50


class TraceEntry(BaseModel):
    """流转轨迹条目（令牌/已办/事件合并时间线）。"""

    model_config = ConfigDict(extra="forbid")

    time: str
    kind: str  # token / task / event
    title: str
    detail: str = ""


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
                    definition_major=row.definition_major,
                    definition_revision=row.definition_revision,
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

    def paged_tasks(
        self,
        *,
        assignee: str | None = None,
        instance_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Page:
        """已办分页查询（UI 列表页数据源）。"""
        from sqlalchemy import func, select

        def _command(ctx: CommandContext) -> Any:
            stmt = select(TaskHistoryRow)
            if assignee:
                stmt = stmt.where(TaskHistoryRow.assignee == assignee)
            if instance_id:
                stmt = stmt.where(TaskHistoryRow.instance_id == instance_id)
            total = ctx.session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = ctx.session.scalars(
                stmt.order_by(TaskHistoryRow.created_at.desc()).offset(offset).limit(limit)
            )
            return Page(
                items=[_history(row) for row in rows],
                total=total or 0,
                offset=offset,
                limit=limit,
            )

        result: Page = self._engine.execute(_command)
        return result

    def trace_instance(self, instance_id: str) -> list[TraceEntry]:
        """实例流转轨迹：令牌 + 已办 + 事件按时间合并（审批轨迹页数据源）。"""

        def _command(ctx: CommandContext) -> Any:
            entries: list[tuple[str, str, str, str]] = []
            for token in self.tokens(instance_id):
                entries.append(
                    (
                        str(token.created_at),
                        "token",
                        f"#{token.token_no} {token.activity_code} → {token.state}",
                        f"同意 {token.approve_count}/{token.item_count}"
                        + (f"，否决 {token.disapprove_count}" if token.disapprove_count else ""),
                    )
                )
            for item in self.task_history(instance_id):
                entries.append(
                    (
                        str(item.created_at),
                        "task",
                        f"{item.activity_code} · {item.assignee} · {item.outcome}",
                        item.comment,
                    )
                )
            for event in self.events(instance_id):
                entries.append(
                    (
                        str(event.occurred_at),
                        "event",
                        event.event_name,
                        str(event.payload),
                    )
                )
            entries.sort(key=lambda e: e[0])
            return [
                TraceEntry(time=time, kind=kind, title=title, detail=detail)
                for time, kind, title, detail in entries
            ]

        result: list[TraceEntry] = self._engine.execute(_command)
        return result

    def archive_instance(self, instance_id: str) -> dict[str, Any]:
        """完结实例归档导出（JSON 可序列化；热表保持小的冷存方案）。"""

        def _command(ctx: CommandContext) -> Any:
            from sqlalchemy import select

            from flowork.persistence.orm import (
                CcItemRow,
                EventOutboxRow,
                ProcessInstanceRow,
                TokenRow,
            )

            instance = ctx.session.get(ProcessInstanceRow, instance_id)
            if instance is None:
                raise InstanceNotFoundError(f"实例不存在：{instance_id}")

            def rows(model: Any, order: Any) -> list[dict[str, Any]]:
                result: list[dict[str, Any]] = []
                for row in ctx.session.scalars(  # type: ignore[var-annotated]
                    select(model).where(model.instance_id == instance_id).order_by(order)
                ):
                    result.append(
                        {c.name: str(getattr(row, c.name)) for c in model.__table__.columns}
                    )
                return result

            archive: dict[str, Any] = {
                "instance": {
                    "id": instance.id,
                    "definition_code": instance.definition_code,
                    "state": instance.state,
                    "business_key": instance.business_key,
                    "start_user": instance.start_user,
                    "started_at": str(instance.started_at),
                    "finished_at": str(instance.finished_at),
                    "variables": dict(instance.variables),
                },
                "tokens": rows(TokenRow, TokenRow.token_no),
                "task_history": rows(TaskHistoryRow, TaskHistoryRow.created_at),
                "cc_items": rows(CcItemRow, CcItemRow.created_at),
                "events": rows(EventOutboxRow, EventOutboxRow.occurred_at),
            }
            return archive

        result: dict[str, Any] = self._engine.execute(_command)
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
