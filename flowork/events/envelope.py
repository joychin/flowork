"""CloudEvents 1.0.2 信封（kernel-v2 spec §10）。

- type＝事件名；id＝outbox 行 id（可排序唯一串，消费端按 id 去重）；
  source＝``flowork://{engine_id}``；data＝事件字段。
- 交付保证：事件与业务变更同一个数据库事务写入 outbox（transactional outbox）；
  分发为至少一次，消费端需幂等（见 consumer.IdempotentSink）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flowork.persistence.orm import EventOutboxRow

__all__ = ["SPEC_VERSION", "envelope_of"]

SPEC_VERSION = "1.0.2"


def envelope_of(row: EventOutboxRow, engine_id: str) -> dict[str, Any]:
    """把一条 outbox 行包装为 CloudEvents 1.0.2 信封（JSON 分布形态）。"""
    occurred_at = row.occurred_at
    time_iso = occurred_at.isoformat() if occurred_at is not None else None
    if isinstance(occurred_at, datetime) and occurred_at.tzinfo is None:
        time_iso = occurred_at.replace(tzinfo=UTC).isoformat()
    return {
        "specversion": SPEC_VERSION,
        "type": row.event_name,
        "id": row.id,
        "source": f"flowork://{engine_id}",
        "time": time_iso,
        "data": dict(row.payload or {}),
        **({"instanceid": row.instance_id} if row.instance_id else {}),
    }
