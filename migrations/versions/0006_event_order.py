"""事件全序复合索引（全仓 review 2026-10-13 批 4 O-1）：wf_event_outbox。

``ORDER BY (occurred_at, id)`` 是 A1 事件全序契约；默认永久保留语义下
（2026-10-13 拍板）全局 events 查询与消费器取批在百万行级是第一个退化点。
零行为变化纯索引。

Revision ID: 0006_event_order
Revises: 0005_draft_layout
Create Date: 2026-10-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_event_order"
down_revision = "0005_draft_layout"
branch_labels = None
depends_on = None


def _has_index(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {i["name"] for i in insp.get_indexes("wf_event_outbox")}


def upgrade() -> None:
    if _has_index("ix_event_order"):  # 基线 create_all 新库已含（幂等跳过，同 0002/0003）
        return
    op.create_index("ix_event_order", "wf_event_outbox", ["occurred_at", "id"])


def downgrade() -> None:
    if not _has_index("ix_event_order"):
        return
    op.drop_index("ix_event_order", table_name="wf_event_outbox")
