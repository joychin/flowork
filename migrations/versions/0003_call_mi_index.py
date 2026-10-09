"""多实例子流程迭代序号（v2.3 第四批 A9）：wf_process_instance.parent_index。

第 k 个 subject 的子实例记 k（0 起）——输出按 index 对齐聚合的锚；
单子/非子流程为 NULL。

Revision ID: 0003_call_mi
Revises: 0002_simulated
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_call_mi"
down_revision = "0002_simulated"
branch_labels = None
depends_on = None


def _has_column(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {c["name"] for c in insp.get_columns("wf_process_instance")}


def upgrade() -> None:
    if _has_column("parent_index"):  # 基线 create_all 新库已含（幂等跳过，同 0002）
        return
    op.add_column("wf_process_instance", sa.Column("parent_index", sa.Integer(), nullable=True))


def downgrade() -> None:
    if not _has_column("parent_index"):
        return
    op.drop_column("wf_process_instance", "parent_index")
