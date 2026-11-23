"""故障单只读查询（与处置面分居守 300 行红线；message_query 同款先例）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from flowork.engine.ops import CommandContext
from flowork.persistence.orm import IncidentRow

__all__ = ["list_incidents"]


def list_incidents(engine: Any, instance_id: str | None = None) -> list[dict[str, Any]]:
    """查询 Incident（open/resolved；含节点定位列）。"""

    def _command(ctx: CommandContext) -> Any:
        stmt = select(IncidentRow).order_by(IncidentRow.created_at.desc())
        if instance_id:
            stmt = stmt.where(IncidentRow.instance_id == instance_id)
        return [
            {
                "id": row.id,
                "instance_id": row.instance_id,
                "message": row.message,
                "error_class": row.error_class,
                "activity_code": row.activity_code,
                "state": row.state,
                "resolution": row.resolution,
            }
            for row in ctx.session.scalars(stmt)
        ]

    incidents: list[dict[str, Any]] = engine.execute(_command)
    return incidents
