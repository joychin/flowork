"""基线迁移（kernel-v2 spec §14 尾注）：全部表＝共享元数据一键建。

基线与引擎启动建表（persistence.db.create_schema）同源同构——
``alembic upgrade head`` 与 ``create_all`` 两条路殊途同归；既有 create_all
库先 ``alembic stamp head`` 对齐版本标记，再走后续增量迁移。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-09
"""

from __future__ import annotations

from alembic import op

import flowork.directory.orm  # noqa: F401  注册目录表（org_*）
import flowork.persistence.orm_v2  # noqa: F401  注册 v2 表（wf_message_* 等）
from flowork.persistence.orm import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
