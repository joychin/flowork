"""目录读面与缓存（M-A 收口）：OrgDirectoryLike 查询方法 + 进程内读缓存。

缓存策略（engine-v1 01 §2）：目录变更频率极低——读全走 ``_read``，任何写操作
经 ``_write_done`` 全清（写走短事务，全清粒度足够；进程内共享，多副本的副本
间失效随开放面批次再议）。``delegation_of`` 窗口随时间滑动，不缓存（见
delegation.py）。本 Mixin 排在组合首位，``users_with_role`` 显式经
RoleDirectoryMixin 实现入缓存（mixin 内 super() 链对 mypy 不可见）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from flowork.directory.common import DirectoryError, _like_prefix
from flowork.directory.orm import OrgUnitRow, OrgUserRow
from flowork.directory.roles import RoleDirectoryMixin
from flowork.participants.org_directory import split_group

_T = TypeVar("_T")


class ReadDirectoryMixin(RoleDirectoryMixin):
    """挂到 DirectoryStore 上：OrgDirectoryLike 读面（全部走进程内缓存）。

    继承 RoleDirectoryMixin 以获得 users_with_role 的未缓存实现（本类包裹
    入缓存）——DirectoryStore 组合时 Read 排首位即生效。
    """

    _factory: sessionmaker[Session]
    _cache: dict[tuple[Any, ...], Any]

    def _write_done(self) -> None:
        raise NotImplementedError

    def _read(self, key: tuple[Any, ...], producer: Callable[[], _T]) -> _T:
        if key not in self._cache:
            self._cache[key] = producer()
        return cast("_T", self._cache[key])

    # —— 读：OrgDirectoryLike 面 ——

    def is_member(self, user: str, group: str) -> bool:
        """人→组（spec §9）：role: 按范围化角色、org: 按直属部门；裸名视同角色。"""

        def produce() -> bool:
            kind, name = split_group(group)
            if not name:
                return False
            if kind == "org":
                return user in self.users_in_department(name)
            return user in self.users_with_role(name)

        return self._read(("is_member", user, group), produce)

    def subjects_in(self, group: str) -> list[str]:
        """组→人：直接成员（不含子部门）。"""

        def produce() -> list[str]:
            kind, name = split_group(group)
            if not name:
                return []
            if kind == "org":
                return self.users_in_department(name)
            return self.users_with_role(name)

        return list(self._read(("subjects_in", group), produce))

    def snapshot(self, subject: str) -> dict[str, object]:
        """身份快照（spec §5.6）：宿主审计策略待接，先返回部门事实。"""

        def produce() -> dict[str, object]:
            department = self.department_of(subject.removeprefix("user:"))
            return {"department": department} if department else {}

        return dict(self._read(("snapshot", subject), produce))

    def users_with_role(self, role: str, *, within: str | None = None) -> list[str]:
        """roles 实现的缓存包裹（显式经 RoleDirectoryMixin，见模块 docstring）。"""

        def produce() -> list[str]:
            return RoleDirectoryMixin.users_with_role(self, role, within=within)

        return list(self._read(("users_with_role", role, within), produce))

    @property
    def admins(self) -> list[str]:
        """平台角色 admin 持有人（02-权限模型 §3；兼 to_admins 兜底名单）。"""
        return self.users_with_role("admin")

    def user_exists(self, user_id: str) -> bool:

        def produce() -> bool:
            with self._factory() as session:
                return session.get(OrgUserRow, user_id) is not None

        return self._read(("user_exists", user_id), produce)

    def manager_of(self, user_id: str) -> str | None:
        """转上级（spec §9 丰富面）：人 → 主属部门主管；`org:` → 部门负责人；
        查无返回 None。

        **单跳不递归**——「主管的主管」由宿主在自己的词汇里组合，不进本方法
        （函数的语义只回答「这个主体的问责上级是谁」）。主体一律裸 id
        （spec §5.4；`agent:` 写法已删、AI 问责链归宿主目录——2026-10-10
        第十六/十七轮）。
        """
        if user_id.startswith("user:"):
            user_id = user_id[5:]

        def produce() -> str | None:
            if user_id.startswith("org:"):
                return self.department_head(user_id[4:])
            if self.user_exists(user_id):
                return self.department_head(self.department_of(user_id) or "")
            return None

        return self._read(("manager_of", user_id), produce)

    def department_of(self, user_id: str) -> str | None:
        def produce() -> str | None:
            with self._factory() as session:
                user = session.get(OrgUserRow, user_id)
                return user.org_unit_code if user else None

        return self._read(("department_of", user_id), produce)

    def department_head(self, department: str) -> str | None:
        def produce() -> str | None:
            with self._factory() as session:
                unit = session.get(OrgUnitRow, department)
                return unit.manager_user_id if unit else None

        return self._read(("department_head", department), produce)

    def parent_department(self, department: str) -> str | None:
        def produce() -> str | None:
            with self._factory() as session:
                unit = session.get(OrgUnitRow, department)
                return unit.parent_code if unit else None

        return self._read(("parent_department", department), produce)

    def users_in_department(self, department: str, *, recursive: bool = False) -> list[str]:
        def produce() -> list[str]:
            with self._factory() as session:
                unit = session.get(OrgUnitRow, department)
                if unit is None:
                    raise DirectoryError(f"部门 {department!r} 不存在")
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

        return list(self._read(("users_in_department", department, recursive), produce))
