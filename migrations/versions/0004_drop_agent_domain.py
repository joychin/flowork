"""Agent 域移除（spec v2.3 第十七轮）：删 org_agent 两表与 acted_as_agent 两列。

用户裁定：引擎对 AI 无审核与治理职能——主体一律裸 id，身份/凭证/问责归宿主
目录；「是否 AI 代办」的归因标记一并移除（actor 字符串已承载归因，宿主如需
人机区分自管身份映射）。基线 create_all 新库已无此表此列（幂等跳过，同 0002）。

Revision ID: 0004_drop_agent
Revises: 0003_call_mi
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_drop_agent"
down_revision = "0003_call_mi"
branch_labels = None
depends_on = None

_TABLES = ("org_agent_credential", "org_agent")
_COLUMNS = (("wf_task_history", "acted_as_agent"), ("wf_variable_history", "acted_as_agent"))


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {
        c["name"] for c in insp.get_columns(table)
    }


def upgrade() -> None:
    existing = _tables()
    for table in _TABLES:
        if table in existing:
            op.drop_table(table)
    for table, column in _COLUMNS:
        if _has_column(table, column):
            op.drop_column(table, column)


def downgrade() -> None:
    # Agent 域已整体移除，downgrade 不重建（零兼容口径，同第十六轮）。
    raise NotImplementedError("agent 域已移除，不提供回滚（spec v2.3 第十七轮）")
