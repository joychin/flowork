"""取人函数库：参与者表达式 ``name(args)`` 的求值（注册表模式）。

内置函数：originator / manager / manager_of / dept_head / users_with_role /
users_in_department / previous_handlers / literal。
自定义：``register_function("my_pick", fn)``。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError

if TYPE_CHECKING:
    from flowork.engine.context import RuntimeNodeContext
    from flowork.participants.org_directory import OrgDirectoryLike

__all__ = ["FunctionCall", "ParticipantFunctions", "UnknownFunction", "parse_expression"]


class UnknownFunction(FloworkError):
    """未注册的取人函数。"""


_EXPRESSION = re.compile(r"^(\w+)\((.*)\)$")
_ARGS = re.compile(r"[,'\"\s]+")


@dataclass
class FunctionCall:
    """一次函数调用（参与者表达式解析结果）。"""

    name: str
    args: list[str] = field(default_factory=list)


def parse_expression(expression: str) -> FunctionCall | None:
    """解析 ``fn(a, b)`` 形式；非函数（字面 id）返回 None。"""
    match = _EXPRESSION.match(expression.strip())
    if not match:
        return None
    raw_args = match.group(2).strip()
    args = [a.strip("'\" ") for a in _ARGS.split(raw_args) if a.strip("'\" ")] if raw_args else []
    return FunctionCall(name=match.group(1), args=args)


@dataclass
class ParticipantFunctions:
    """取人函数注册表：name → callable(ctx, org, args) -> list[str]。"""

    _registry: dict[str, Callable[..., list[str]]] = field(default_factory=dict)

    def register(self, name: str, fn: Callable[..., list[str]], *, replace: bool = False) -> None:
        if name in self._registry and not replace:
            raise ValueError(f"取人函数 {name!r} 已注册（replace=True 可覆盖）")
        self._registry[name] = fn

    def call(
        self, name: str, ctx: RuntimeNodeContext, org: OrgDirectoryLike | None, args: list[str]
    ) -> list[str]:
        if name not in self._registry:
            known = ", ".join(self._registry)
            raise UnknownFunction(f"未注册的取人函数：{name!r}（已注册：{known}）")
        return self._registry[name](ctx, org, *args)

    def names(self) -> tuple[str, ...]:
        return tuple(self._registry)


def _org_required(org: OrgDirectoryLike | None) -> OrgDirectoryLike:
    if org is None:
        raise UnknownFunction("本函数需要配置组织目录（EngineConfig.org）")
    return org


def _originator(ctx: RuntimeNodeContext, org: OrgDirectoryLike | None) -> list[str]:
    return [ctx.instance.start_user]


def _manager(ctx: RuntimeNodeContext, org: OrgDirectoryLike | None) -> list[str]:
    return [m for m in [_org_required(org).manager_of(ctx.instance.start_user)] if m]


def _manager_of(ctx: RuntimeNodeContext, org: OrgDirectoryLike | None, user: str = "") -> list[str]:
    user_id = str(ctx.variables.get(user, user))
    return [m for m in [_org_required(org).manager_of(user_id)] if m]


def _dept_head(
    ctx: RuntimeNodeContext, org: OrgDirectoryLike | None, department: str = ""
) -> list[str]:
    directory = _org_required(org)
    dept = str(ctx.variables.get(department, department)) if department else ""
    if not dept:
        dept = directory.department_of(ctx.instance.start_user) or ""
    return [h for h in [directory.department_head(dept)] if h]


def _users_with_role(
    ctx: RuntimeNodeContext, org: OrgDirectoryLike | None, role: str = ""
) -> list[str]:
    directory = _org_required(org)
    role_name = str(ctx.variables.get(role, role)) if role else ""
    within = directory.department_of(ctx.instance.start_user)
    return directory.users_with_role(role_name, within=within)


def _users_in_department(
    ctx: RuntimeNodeContext, org: OrgDirectoryLike | None, department: str = "", recursive: str = ""
) -> list[str]:
    directory = _org_required(org)
    dept = str(ctx.variables.get(department, department)) if department else ""
    if not dept:
        dept = directory.department_of(ctx.instance.start_user) or ""
    return directory.users_in_department(dept, recursive=bool(recursive))


def _previous_handlers(
    ctx: RuntimeNodeContext, org: OrgDirectoryLike | None, activity: str = ""
) -> list[str]:
    """上一节点（或指定节点）的办理人。"""
    from sqlalchemy import select

    from flowork.persistence.orm import TaskHistoryRow

    assert ctx.token is not None
    activity_code = activity or ctx.token.source_activity_code or ""
    if not activity_code:
        return []
    rows = ctx.session.scalars(
        select(TaskHistoryRow).where(
            TaskHistoryRow.instance_id == ctx.instance.id,
            TaskHistoryRow.activity_code == activity_code,
            TaskHistoryRow.outcome != "cancelled",
        )
    )
    return list(dict.fromkeys(row.assignee for row in rows))


def _literal(ctx: RuntimeNodeContext, org: OrgDirectoryLike | None, *users: str) -> list[str]:
    return [u for u in users if u]


def builtin_functions() -> ParticipantFunctions:
    """注册全部内置取人函数。"""
    functions = ParticipantFunctions()
    functions.register("originator", _originator, replace=True)
    functions.register("manager", _manager, replace=True)
    functions.register("manager_of", _manager_of, replace=True)
    functions.register("dept_head", _dept_head, replace=True)
    functions.register("users_with_role", _users_with_role, replace=True)
    functions.register("users_in_department", _users_in_department, replace=True)
    functions.register("previous_handlers", _previous_handlers, replace=True)
    functions.register("literal", _literal, replace=True)
    return functions


_ = Any  # typing 占位
