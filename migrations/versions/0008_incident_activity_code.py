"""故障单节点定位（2026-10-21 内核 review P0-1）：wf_incident.activity_code。

错误归因帧标注落列——skip 处置据此只砍故障节点的令牌（修前对全部未完令牌
生效，并行分支上的人工待办会被静默取消）。无节点作用域的故障为 NULL。

Revision ID: 0008_incident_activity_code
Revises: 0007_designer_scenario
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_incident_activity_code"
down_revision = "0007_designer_scenario"
branch_labels = None
depends_on = None


def _has_column(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {c["name"] for c in insp.get_columns("wf_incident")}


def upgrade() -> None:
    if _has_column("activity_code"):  # 基线 create_all 新库已含（幂等跳过，同 0003）
        return
    op.add_column("wf_incident", sa.Column("activity_code", sa.String(64), nullable=True))


def downgrade() -> None:
    if not _has_column("activity_code"):
        return
    op.drop_column("wf_incident", "activity_code")
