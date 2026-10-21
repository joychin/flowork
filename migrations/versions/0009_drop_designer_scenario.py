"""删除设计器场景表 wf_designer_scenario（2026-10-21 内核 review P2）。

场景已改文件事实源（``<code>.scenario.json`` 随定义同目录），表的读写方
（engine/scenario_store.py）随文件化批退役——本迁移清掉每个宿主库里
这张无人读写的表。downgrade 不提供（0004 删表先例）。

Revision ID: 0009_drop_designer_scenario
Revises: 0008_incident_activity_code
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_drop_designer_scenario"
down_revision = "0008_incident_activity_code"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in insp.get_table_names()


def upgrade() -> None:
    if _has_table("wf_designer_scenario"):
        op.drop_index("ix_scenario_code", table_name="wf_designer_scenario")
        op.drop_table("wf_designer_scenario")


def downgrade() -> None:
    raise NotImplementedError("删表迁移不提供 downgrade（0004 先例）")
