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

__all__ = ["OrgConfigError", "OrgDirectory", "OrgDirectoryLike"]


class OrgDirectoryLike(Protocol):
    """引擎消费的目录查询面：静态 OrgDirectory 与持久化 DirectoryStore 均满足。"""

    def user_exists(self, user_id: str) -> bool: ...
    def manager_of(self, user_id: str) -> str | None: ...
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

    # —— 查询 ——

    def user_exists(self, user_id: str) -> bool:
        return user_id in self.users

    def manager_of(self, user_id: str) -> str | None:
        info = self.users.get(user_id)
        manager = info.get("manager") if info else None
        return str(manager) if manager else None

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


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
