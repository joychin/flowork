"""组织目录（YAML 静态配置实现，OrgDirectory 端口的默认实现）。

配置样例::

    users:
      u_alice: {manager: u_bob, department: d_eng, roles: [eng_lead]}
    departments:
      d_eng: {head: u_bob, parent: d_all}
    delegations:
      - {from: u_bob, to: u_dave, until: "2026-12-31"}
    admins: [u_admin]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from flowork.errors import FloworkError

__all__ = ["OrgConfigError", "OrgDirectory", "OrgDirectoryLike", "split_group"]


class OrgDirectoryLike(Protocol):
    """引擎消费的目录查询面：静态 OrgDirectory 与持久化 DirectoryStore 均满足。

    v2.1（spec §9）：前四个方法（is_member/subjects_in/manager_of/snapshot）为引擎
    必需的最小面——关系判断（人→组）与身份定格是引擎自己的行为；其余为宿主函数
    （flowork.contrib）与既有解析管线使用的丰富面，实现方可按需退化。
    """

    def is_member(self, user: str, group: str) -> bool: ...
    def subjects_in(self, group: str) -> list[str]: ...
    def manager_of(self, user_id: str) -> str | None: ...
    def snapshot(self, subject: str) -> dict[str, Any]: ...
    def user_exists(self, user_id: str) -> bool: ...
    def department_of(self, user_id: str) -> str | None: ...
    def department_head(self, department: str) -> str | None: ...
    def parent_department(self, department: str) -> str | None: ...
    def users_with_role(self, role: str, *, within: str | None = None) -> list[str]: ...
    def users_in_department(self, department: str, *, recursive: bool = False) -> list[str]: ...
    def delegation_of(self, user_id: str, when: datetime | None = None) -> str | None: ...
    @property
    def admins(self) -> list[str]: ...


class OrgConfigError(FloworkError):
    """组织配置非法。"""


@dataclass(frozen=True)
class Delegation:
    """审批委托：from → to，有效期 [since, until]。"""

    delegate_from: str
    delegate_to: str
    since: date | None = None
    until: date | None = None

    def active_at(self, when: date) -> bool:
        if self.since and when < self.since:
            return False
        return not (self.until and when > self.until)


@dataclass
class OrgDirectory:
    """静态组织目录：用户/部门/角色/委托/管理员。"""

    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    departments: dict[str, dict[str, Any]] = field(default_factory=dict)
    delegations: list[Delegation] = field(default_factory=list)
    admins: list[str] = field(default_factory=list)

    # —— 构造 ——

    @classmethod
    def from_yaml(cls, path: str | Path) -> OrgDirectory:
        text = Path(path).read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise OrgConfigError(f"组织配置 YAML 解析失败：{exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrgDirectory:
        delegations = [
            Delegation(
                delegate_from=str(item["from"]),
                delegate_to=str(item["to"]),
                since=_as_date(item.get("since")),
                until=_as_date(item.get("until")),
            )
            for item in data.get("delegations", [])
        ]
        return cls(
            users=dict(data.get("users", {})),
            departments=dict(data.get("departments", {})),
            delegations=delegations,
            admins=[str(a) for a in data.get("admins", [])],
        )

    # —— 查询：v2.1 引擎必需四方法（spec §9）——

    def is_member(self, user: str, group: str) -> bool:
        """人→组：``role:{name}`` 按角色、``org:{code}`` 按直属部门；裸名视同角色。"""
        kind, name = split_group(group)
        if not name:
            return False
        if kind == "org":
            info = self.users.get(user)
            return bool(info and info.get("department") == name)
        return bool(self.users.get(user)) and name in (self.users[user].get("roles") or [])

    def subjects_in(self, group: str) -> list[str]:
        """组→人：直接成员（不含子部门）。"""
        kind, name = split_group(group)
        if not name:
            return []
        if kind == "org":
            return self.users_in_department(name)
        return self.users_with_role(name)

    def manager_of(self, user_id: str) -> str | None:
        """转上级：吃 user id 返回其 manager；吃 ``org:{code}`` 返回部门负责人。

        ``agent:{id}`` → 本实现**没有 agent 表**（静态目录只描述人），返回 None；
        要 agent 问责链请用持久化目录（DirectoryStore，其 manager_of 走 owner）
        或宿主自实现。单跳不递归（spec §9 丰富面口径，2026-10-09 第十四轮）。
        """
        if user_id.startswith("org:"):
            return self.department_head(user_id[4:])
        info = self.users.get(user_id.removeprefix("user:").removeprefix("agent:"))
        manager = info.get("manager") if info else None
        return str(manager) if manager else None

    def snapshot(self, subject: str) -> dict[str, Any]:
        """身份快照（spec §5.6）：静态目录可给出的岗位事实；未知主体返回 {}。"""
        info = self.users.get(subject.removeprefix("user:"))
        if not info:
            return {}
        return {
            "department": info.get("department"),
            "roles": list(info.get("roles") or []),
        }

    # —— 查询：丰富面（contrib 词汇与既有管线使用）——

    def user_exists(self, user_id: str) -> bool:
        return user_id in self.users

    def department_of(self, user_id: str) -> str | None:
        info = self.users.get(user_id)
        dept = info.get("department") if info else None
        return str(dept) if dept else None

    def department_head(self, department: str) -> str | None:
        info = self.departments.get(department)
        head = info.get("head") if info else None
        return str(head) if head else None

    def parent_department(self, department: str) -> str | None:
        info = self.departments.get(department)
        parent = info.get("parent") if info else None
        return str(parent) if parent else None

    def users_with_role(self, role: str, *, within: str | None = None) -> list[str]:
        return [
            user_id for user_id, info in self.users.items() if role in (info.get("roles") or [])
        ]

    def users_in_department(self, department: str, *, recursive: bool = False) -> list[str]:
        result = [
            user_id for user_id, info in self.users.items() if info.get("department") == department
        ]
        if recursive:
            for child in self._child_departments(department):
                result.extend(self.users_in_department(child, recursive=True))
        return result

    def _child_departments(self, department: str) -> list[str]:
        return [code for code, info in self.departments.items() if info.get("parent") == department]

    def delegation_of(self, user_id: str, when: datetime | None = None) -> str | None:
        """生效委托：返回受托人（无则 None）。"""
        from flowork.clock import utc_now

        today = (when or utc_now()).date()
        for item in self.delegations:
            if item.delegate_from == user_id and item.active_at(today):
                return item.delegate_to
        return None


def split_group(group: str) -> tuple[str, str]:
    """拆组引用：``role:finance``→("role","finance")；裸名视同 role。"""
    if group.startswith(("role:", "org:")):
        kind, _, name = group.partition(":")
        return kind, name
    return "role", group


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
