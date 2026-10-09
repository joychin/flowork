"""模拟实例标记（v2.3 第三批 A10）：wf_process_instance.simulated 布尔列。

模拟实例（试跑）——service_task 恒 stub、不建任务/传阅/Job、事件不投 outbox、
查询面默认隔离。与共享元数据同源（orm.py 加列后 create_all 新库自带；既有库
走本迁移）。

Revision ID: 0002_simulated
Revises: 0001_baseline
Create Date: 2026-10-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_simulated"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("wf_process_instance") as batch:
        batch.add_column(
            sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("wf_process_instance") as batch:
        batch.drop_column("simulated")
