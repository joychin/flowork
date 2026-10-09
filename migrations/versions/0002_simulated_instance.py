"""模拟实例标记（v2.3 第三批 A10）：wf_process_instance.simulated 布尔列。

模拟实例（试跑）——service_task 恒 stub、不建任务/传阅/Job、事件不投 outbox、
查询面默认隔离。与共享元数据同源（orm.py 加列后 create_all 新库自带；既有库
走本迁移）。

Revision ID: 0002_simulated
Revises: 0001_baseline
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_simulated"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _has_column(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {c["name"] for c in insp.get_columns("wf_process_instance")}


def upgrade() -> None:
    # 基线 0001=create_all（共享元数据）——新库已含本列则幂等跳过（仅旧库需要追加）
    if _has_column("simulated"):
        return
    op.add_column(
        "wf_process_instance",
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    if not _has_column("simulated"):
        return
    op.drop_column("wf_process_instance", "simulated")
