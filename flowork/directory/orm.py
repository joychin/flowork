"""目录持久化表：挂在引擎共享元数据上（create_schema 一并建表）。

v1 竖切仅两张表（部门/用户）；角色与授权表（role/role_assignment/agent）随 M-A 全量补充，
见 docs/product/engine-v1/01-身份与组织模型.md §3。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from flowork.clock import utc_now
from flowork.persistence.orm import Base


class OrgUnitRow(Base):
    """部门（树）。manager_user_id 是部门属性——"直接主管"由主属部门解析。"""

    __tablename__ = "org_unit"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    parent_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manager_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    path: Mapped[str] = mapped_column(String(1000))  # 含自身，如 /d_all/d_eng
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class OrgUserRow(Base):
    """用户（主属部门，单主属；兼岗进 backlog）。"""

    __tablename__ = "org_user"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    org_unit_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/suspended
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
