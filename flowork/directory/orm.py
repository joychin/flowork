"""目录持久化表：挂在引擎共享元数据上（create_schema 一并建表）。

部门/用户/角色/授权/委托。Agent 身份表已删（引擎对 AI
无治理职能——主体一律裸 id，身份与凭证归宿主目录）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String
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


class DelegationRow(Base):
    """审批委托：from 在 [since, until] 窗口内待办转 to。

    单跳不递归（A→B、B→C 时 A 的待办只到 B）；窗口端点 None=该侧无限。
    """

    __tablename__ = "org_delegation"
    __table_args__ = (Index("ix_delegation_from", "delegate_from"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    delegate_from: Mapped[str] = mapped_column(String(64))
    delegate_to: Mapped[str] = mapped_column(String(64))
    since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class DirectoryAuditRow(Base):
    """目录写审计（追加只写）：谁在何时改了什么（部门/用户/角色/委托/Agent）。"""

    __tablename__ = "org_directory_audit"
    __table_args__ = (Index("ix_dir_audit_time", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64))  # org_unit.create / role.assign / ...
    entity: Mapped[str] = mapped_column(String(200))  # org_unit:d_eng / user:u_a / ...
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    acted_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
