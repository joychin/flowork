"""设计器场景表（designer-simulation-plan 批 4）：wf_designer_scenario。

可复跑脚本的持久化（steps/controls/assertions + 最近一次运行结论）——
设计器专属资产，与引擎定义生命周期无关。基线 create_all 新库已含本表，
升级时按存在性幂等跳过（同 0002/0003/0005 先例）。

Revision ID: 0007_designer_scenario
Revises: 0006_event_order
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_designer_scenario"
down_revision = "0006_event_order"
branch_labels = None
depends_on = None


def _has_table() -> bool:
    insp = sa.inspect(op.get_bind())
    return "wf_designer_scenario" in insp.get_table_names()


def upgrade() -> None:
    if _has_table():  # 基线 create_all 新库已含
        return
    op.create_table(
        "wf_designer_scenario",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("definition_code", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("script", sa.JSON(), nullable=False),
        sa.Column("last_ok", sa.Boolean(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scenario_code", "wf_designer_scenario", ["definition_code", "name"])


def downgrade() -> None:
    if not _has_table():
        return
    op.drop_table("wf_designer_scenario")
