"""持久化目录（M-A）：部门树 + 用户 + 角色范围化授权 + OrgDirectory 查询面。

语义对齐 docs/product/engine-v1/01-身份与组织模型.md §2：
- manager_of(user) = user 主属部门的 manager_user_id（部门属性，非用户属性）；
- users_with_role 范围解析见 roles.py（子树→逐级向上→全局，最近命中层胜出）；
- delegation_of 为竖切占位（委托表随 M-A 全量落地）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from flowork.directory.agents import AgentDirectoryMixin
from flowork.directory.common import DirectoryError, _like_prefix, _validate_code
from flowork.directory.orm import OrgUnitRow, OrgUserRow
from flowork.directory.roles import RoleDirectoryMixin

__all__ = ["DirectoryError", "DirectoryStore", "OrgUnitInfo", "UserInfo"]

_MAX_DEPTH = 1000


@dataclass(frozen=True)
class OrgUnitInfo:
    code: str
    name: str
    parent_code: str | None
    manager_user_id: str | None
    path: str


@dataclass(frozen=True)
class UserInfo:
    user_id: str
    name: str
    org_unit_code: str | None
    status: str


class DirectoryStore(AgentDirectoryMixin, RoleDirectoryMixin):
    """DB 版组织目录：写操作短事务，读操作满足 OrgDirectoryLike 协议。"""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        bind = factory.kw.get("bind")
        if isinstance(bind, Engine):
            OrgUnitRow.metadata.create_all(bind)  # 幂等：org_* 表挂共享元数据

    # —— 写：部门 ——

    def create_org_unit(
        self,
        code: str,
        *,
        name: str = "",
        parent_code: str | None = None,
        manager_user_id: str | None = None,
    ) -> OrgUnitInfo:
        _validate_code("部门", code)
        with self._factory() as session, session.begin():
            if session.get(OrgUnitRow, code) is not None:
                raise DirectoryError(f"部门 {code!r} 已存在")
            path = f"/{code}"
            if parent_code is not None:
                if parent_code == code:
                    raise DirectoryError("部门的上级不能是自身")
                parent = self._unit(session, parent_code)
                path = f"{parent.path}/{code}"
            session.add(
                OrgUnitRow(
                    code=code,
                    name=name,
                    parent_code=parent_code,
                    manager_user_id=manager_user_id,
                    path=path,
                )
            )
        return OrgUnitInfo(code, name, parent_code, manager_user_id, path)

    def move_org_unit(self, code: str, *, parent_code: str | None) -> OrgUnitInfo:
        with self._factory() as session, session.begin():
            unit = self._unit(session, code)
            if parent_code is not None:
                if parent_code == code:
                    raise DirectoryError("部门的上级不能是自身")
                self._unit(session, parent_code)
                self._assert_not_ancestor(session, code, parent_code)
            unit.parent_code = parent_code
            info = self._recompute_subtree_paths(session, unit, parent_code)
        return info

    def set_org_unit_manager(self, code: str, *, manager_user_id: str | None) -> None:
        with self._factory() as session, session.begin():
            self._unit(session, code).manager_user_id = manager_user_id

    def delete_org_unit(self, code: str) -> None:
        with self._factory() as session, session.begin():
            self._unit(session, code)
            children = session.scalars(
                select(OrgUnitRow).where(OrgUnitRow.parent_code == code)
            ).all()
            if children:
                raise DirectoryError(f"部门 {code!r} 存在子部门，需先迁移")
            active_users = session.scalars(
                select(OrgUserRow).where(
                    OrgUserRow.org_unit_code == code, OrgUserRow.status == "active"
                )
            ).all()
            if active_users:
                raise DirectoryError(f"部门 {code!r} 存在在职用户，需先迁移")
            session.query(OrgUnitRow).filter(OrgUnitRow.code == code).delete()

    # —— 写：用户 ——

    def create_user(
        self,
        user_id: str,
        *,
        name: str = "",
        org_unit_code: str | None = None,
        status: str = "active",
    ) -> UserInfo:
        _validate_code("用户", user_id)
        with self._factory() as session, session.begin():
            if session.get(OrgUserRow, user_id) is not None:
                raise DirectoryError(f"用户 {user_id!r} 已存在")
            if org_unit_code is not None:
                self._unit(session, org_unit_code)
            session.add(
                OrgUserRow(user_id=user_id, name=name, org_unit_code=org_unit_code, status=status)
            )
        return UserInfo(user_id, name, org_unit_code, status)

    def set_user_status(self, user_id: str, status: str) -> None:
        with self._factory() as session, session.begin():
            self._user(session, user_id).status = status

    # —— 读：OrgDirectoryLike 面 ——

    def user_exists(self, user_id: str) -> bool:
        with self._factory() as session:
            return session.get(OrgUserRow, user_id) is not None

    def manager_of(self, user_id: str) -> str | None:
        return self.department_head(self.department_of(user_id) or "")

    def department_of(self, user_id: str) -> str | None:
        with self._factory() as session:
            user = session.get(OrgUserRow, user_id)
            return user.org_unit_code if user else None

    def department_head(self, department: str) -> str | None:
        with self._factory() as session:
            unit = session.get(OrgUnitRow, department)
            return unit.manager_user_id if unit else None

    def parent_department(self, department: str) -> str | None:
        with self._factory() as session:
            unit = session.get(OrgUnitRow, department)
            return unit.parent_code if unit else None

    def users_in_department(self, department: str, *, recursive: bool = False) -> list[str]:
        with self._factory() as session:
            unit = self._unit(session, department)
            if recursive:
                rows = session.scalars(
                    select(OrgUnitRow.code).where(
                        OrgUnitRow.path.like(_like_prefix(f"{unit.path}/"), escape="\\")
                    )
                ).all()
                codes = {department, *rows}
            else:
                codes = {department}
            users = session.scalars(
                select(OrgUserRow.user_id).where(
                    OrgUserRow.org_unit_code.in_(codes), OrgUserRow.status == "active"
                )
            ).all()
            return list(users)

    def delegation_of(self, user_id: str, when: datetime | None = None) -> str | None:
        return None  # 委托表随 M-A 全量落地（01 §5）

    @property
    def admins(self) -> list[str]:
        return []  # M-A 全量：由平台角色 admin 档提供（02-权限模型 §3）

    # —— 内部 ——

    def _unit(self, session: Session, code: str) -> OrgUnitRow:
        row = session.get(OrgUnitRow, code)
        if row is None:
            raise DirectoryError(f"部门 {code!r} 不存在")
        return row

    def _user(self, session: Session, user_id: str) -> OrgUserRow:
        row = session.get(OrgUserRow, user_id)
        if row is None:
            raise DirectoryError(f"用户 {user_id!r} 不存在")
        return row

    def _assert_not_ancestor(self, session: Session, code: str, parent_code: str) -> None:
        current: str | None = parent_code
        for _ in range(_MAX_DEPTH):
            if current is None:
                return
            if current == code:
                raise DirectoryError("不能把部门移动到其子孙部门之下（环）")
            row = session.get(OrgUnitRow, current)
            if row is None:
                raise DirectoryError(f"上级链上的部门 {current!r} 不存在（数据损坏）")
            current = row.parent_code
        raise DirectoryError("部门上级链超过最大深度（疑似数据损坏）")

    def _recompute_subtree_paths(
        self, session: Session, unit: OrgUnitRow, parent_code: str | None
    ) -> OrgUnitInfo:
        if parent_code is None:
            unit.path = f"/{unit.code}"
        else:
            parent = self._unit(session, parent_code)
            unit.path = f"{parent.path}/{unit.code}"
        children_map: dict[str | None, list[OrgUnitRow]] = {}
        for row in session.scalars(select(OrgUnitRow)).all():
            children_map.setdefault(row.parent_code, []).append(row)
        frontier = [unit]
        while frontier:
            node = frontier.pop()
            for child in children_map.get(node.code, []):
                child.path = f"{node.path}/{child.code}"
                frontier.append(child)
        session.flush()
        return OrgUnitInfo(unit.code, unit.name, unit.parent_code, unit.manager_user_id, unit.path)
