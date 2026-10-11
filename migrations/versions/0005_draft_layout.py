"""设计器草稿布局分离（设计器插件计划批 C4）：wf_designer_draft.layout。

画布坐标从草稿 content（语义 JSON）拆到独立 layout 列——定义导出/Git 镜像
不再被展示坐标污染。旧行 layout 为 NULL＝未迁移，读取时从 content 惰性提取
（designer_drafts.designer_draft），首次保存后落新格式。

Revision ID: 0005_draft_layout
Revises: 0004_drop_agent
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_draft_layout"
down_revision = "0004_drop_agent"
branch_labels = None
depends_on = None


def _has_column(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {c["name"] for c in insp.get_columns("wf_designer_draft")}


def upgrade() -> None:
    if _has_column("layout"):  # 基线 create_all 新库已含（幂等跳过，同 0002/0003）
        return
    op.add_column("wf_designer_draft", sa.Column("layout", sa.JSON(), nullable=True))


def downgrade() -> None:
    if not _has_column("layout"):
        return
    op.drop_column("wf_designer_draft", "layout")
