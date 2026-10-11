"""流转轨迹（HistoryService 组合件，入口 engine.history.trace_instance 不变）。

从 history_service.py 拆出（单文件 ≤300 行红线；code-review 2026-10 P2 批
达 304 行）：令牌/已办/事件合并时间线。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from flowork.engine.ops import CommandContext

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["TraceEntry"]


class TraceEntry(BaseModel):
    """流转轨迹条目（令牌/已办/事件合并时间线）。"""

    model_config = ConfigDict(extra="forbid")

    time: str
    kind: str  # token / task / event
    title: str
    detail: str = ""


class TraceMixin:
    """trace_instance 方法体（HistoryService 经 Mixin 组合获得）。"""

    _engine: ProcessEngine  # 由 HistoryService.__init__ 提供

    def tokens(self, instance_id: str) -> list[Any]:
        """令牌查询由 HistoryService 提供（Mixin 桩，MRO 前位被遮蔽）。"""
        raise NotImplementedError

    def task_history(self, instance_id: str) -> list[Any]:
        """已办查询由 HistoryService 提供（Mixin 桩，MRO 前位被遮蔽）。"""
        raise NotImplementedError

    def events(self, instance_id: str) -> list[Any]:
        """事件查询由 HistoryService 提供（Mixin 桩，MRO 前位被遮蔽）。"""
        raise NotImplementedError

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
