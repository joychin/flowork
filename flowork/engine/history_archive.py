"""实例归档导出面（HistoryService 组合件，入口类不变）。

从 history_service.py 拆出（单文件 ≤300 行红线）；HistoryService 经
Mixin 组合获得本面。热表保持小的冷存方案：归档 JSON 可序列化全量导出。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError
from flowork.persistence.orm import (
    CcItemRow,
    EventOutboxRow,
    ProcessInstanceRow,
    TaskHistoryRow,
    TokenRow,
)

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["ArchiveMixin"]


class ArchiveMixin:
    """完结实例归档导出；宿主 HistoryService 提供 _engine/execute。"""

    _engine: ProcessEngine

    def archive_instance(self, instance_id: str) -> dict[str, Any]:
        """完结实例归档导出（JSON 可序列化；热表保持小的冷存方案）。"""

        def _command(ctx: CommandContext) -> Any:
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

            return {
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

        result: dict[str, Any] = self._engine.execute(_command)
        return result
