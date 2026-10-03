"""目录持久化表：挂在引擎共享元数据上（create_schema 一并建表）。

v1 竖切仅两张表（部门/用户）；角色与授权表（role/role_assignment/agent）随 M-A 全量补充，
见 docs/product/engine-v1/01-身份与组织模型.md §3。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
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


class RoleRow(Base):
    """角色。default_scope_org_unit 为空 = 默认全局；授权行未显式给范围时继承此默认。"""

    __tablename__ = "org_role"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    default_scope_org_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class RoleAssignmentRow(Base):
    """角色授权行：user × role × 生效范围（None=继承角色默认）。"""

    __tablename__ = "org_role_assignment"
    __table_args__ = (Index("ix_role_assign", "role_code", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    role_code: Mapped[str] = mapped_column(String(64))
    org_unit_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class AgentRow(Base):
    """Agent 一等身份：负责人（问责链 root）/所属部门/可选代表某人/状态机。

    status: trial → active ⇄ suspended（见 agents.py 状态机）。
    """

    __tablename__ = "org_agent"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    owner_user_id: Mapped[str] = mapped_column(String(64))
    org_unit_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    on_behalf_of_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="trial")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class AgentCredentialRow(Base):
    """Agent 凭证：仅存 scrypt 哈希；吊销/停用即时失效（verify 读当前状态）。"""

    __tablename__ = "org_agent_credential"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64))
    secret_hash: Mapped[str] = mapped_column(String(128))
    salt: Mapped[str] = mapped_column(String(32))
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
