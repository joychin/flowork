"""HistoryService：只读查询（事件/已办/实例/令牌）；归档导出拆 history_archive.py。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from flowork.engine.history_archive import ArchiveMixin
from flowork.engine.history_token import HistoryTokenMixin
from flowork.engine.history_trace import TraceEntry, TraceMixin
from flowork.engine.ops import CommandContext
from flowork.persistence.orm import (
    EventOutboxRow,
    ProcessInstanceRow,
    TaskHistoryRow,
)
from flowork.tasks.models import (
    EventInfo,
    InstanceInfo,
    RouteDecisionInfo,
    TaskHistoryInfo,
)

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["HistoryService", "Page", "TraceEntry"]

T = TypeVar("T")


class Page(BaseModel):
    """分页结果。"""

    model_config = ConfigDict(extra="forbid")
    items: list[Any]
    total: int
    offset: int = 0
    limit: int = 50


class HistoryService(HistoryTokenMixin, ArchiveMixin, TraceMixin):
    """历史与只读查询服务（待办查询在 TaskService.list_tasks）。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine

    def events(
        self,
        instance_id: str | None = None,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[EventInfo]:  # offset＝REST /v2 分页探测取数用

        def _command(ctx: CommandContext) -> Any:
            # 全局取最近，实例维度 asc（可读）；微秒平局按可排序 id 定序。
            if instance_id is None:
                stmt = select(EventOutboxRow).order_by(
                    EventOutboxRow.occurred_at.desc(), EventOutboxRow.id.desc()
                )
            else:
                stmt = select(EventOutboxRow).order_by(
                    EventOutboxRow.occurred_at, EventOutboxRow.id
                )
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
                for row in ctx.session.scalars(stmt.offset(offset).limit(limit))
            ]

        result: list[EventInfo] = self._engine.execute(_command)
        return result

    def route_decisions(self, instance_id: str) -> list[RouteDecisionInfo]:
        """路由决策审计：重放解释用，按时间正序。"""
        from flowork.persistence.orm_v2 import RouteDecisionRow

        def _command(ctx: CommandContext) -> Any:
            stmt = (
                select(RouteDecisionRow)
                .where(RouteDecisionRow.instance_id == instance_id)
                # (created_at, id) 次键：一次命令多行平局按可排序 id 定序
                .order_by(RouteDecisionRow.created_at, RouteDecisionRow.id)
            )
            return [
                RouteDecisionInfo(
                    id=row.id,
                    instance_id=row.instance_id,
                    token_id=row.token_id,
                    source_activity=row.source_activity,
                    mode=row.mode,
                    edges=list(row.edges or []),
                    selected=list(row.selected or []),
                    snapshot=dict(row.snapshot or {}),
                    created_at=row.created_at,
                )
                for row in ctx.session.scalars(stmt)
            ]

        result: list[RouteDecisionInfo] = self._engine.execute(_command)
        return result

    def task_history(
        self,
        instance_id: str | None = None,
        assignee: str | None = None,
        *,
        limit: int = 200,
        include_simulated: bool = False,
    ) -> list[TaskHistoryInfo]:
        """已办查询（实例定向不隔离；全局/按人默认排除模拟）。"""

        def _command(ctx: CommandContext) -> Any:
            # 全局/按人查询取最近；实例维度保持 asc
            order = (
                TaskHistoryRow.created_at.desc()
                if instance_id is None and assignee is None
                else TaskHistoryRow.created_at
            )
            stmt = select(TaskHistoryRow).order_by(order)
            if instance_id:
                stmt = stmt.where(TaskHistoryRow.instance_id == instance_id)
            elif not include_simulated:
                from flowork.engine.sim_isolation import exclude_simulated

                stmt = exclude_simulated(stmt, TaskHistoryRow.instance_id)
            if assignee:
                stmt = stmt.where(TaskHistoryRow.assignee == assignee)
            return [_history(row) for row in ctx.session.scalars(stmt.limit(limit))]

        result: list[TaskHistoryInfo] = self._engine.execute(_command)
        return result

    def instances(
        self,
        *,
        state: str | None = None,
        definition_code: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_simulated: bool = False,
    ) -> list[InstanceInfo]:
        """实例查询（默认隔离模拟实例）；offset＝REST 分页探测用。"""

        def _command(ctx: CommandContext) -> Any:
            # id 次键：同秒启动平局下翻页可能重漏行
            stmt = select(ProcessInstanceRow).order_by(
                ProcessInstanceRow.started_at.desc(), ProcessInstanceRow.id
            )
            if not include_simulated:
                stmt = stmt.where(ProcessInstanceRow.simulated.is_(False))
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
                    simulated=row.simulated,
                )
                for row in ctx.session.scalars(stmt.offset(offset).limit(limit))
            ]

        result: list[InstanceInfo] = self._engine.execute(_command)
        return result

    def paged_tasks(
        self,
        *,
        assignee: str | None = None,
        instance_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
        include_simulated: bool = False,
    ) -> Page:
        """已办分页查询（UI 列表页数据源）。模拟实例默认隔离（定向实例不隔离）。"""
        from sqlalchemy import func, select

        def _command(ctx: CommandContext) -> Any:
            stmt = select(TaskHistoryRow)
            if assignee:
                stmt = stmt.where(TaskHistoryRow.assignee == assignee)
            if instance_id:
                stmt = stmt.where(TaskHistoryRow.instance_id == instance_id)
            elif not include_simulated:
                from flowork.engine.sim_isolation import exclude_simulated

                stmt = exclude_simulated(stmt, TaskHistoryRow.instance_id)
            total = ctx.session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = ctx.session.scalars(
                stmt.order_by(TaskHistoryRow.created_at.desc(), TaskHistoryRow.id)
                .offset(offset)
                .limit(limit)
            )
            return Page(
                items=[_history(row) for row in rows], total=total or 0, offset=offset, limit=limit
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
        cause_token_id=row.cause_token_id,
        actor_context=dict(row.actor_context or {}),
    )
