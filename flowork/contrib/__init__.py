"""contrib 便利包（v2.1，spec §5.5/C.4）：可选的取人词汇，一行挂上。

纯基于 OrgDirectory 四方法端口实现，内核注册表出厂为空——本包是「官方插件」，
内核（engine/participants/expr/definitions 等模块）import 本包即违反边界铁律
（import-linter 红灯，见 pyproject [tool.importlinter]）。宿主用法::

    from flowork import contrib
    config = EngineConfig(org=org, participant_functions=contrib.org_vocabulary())

词汇（三个）：
- ``manager_of(one_user) → one_user``：吃 user:/agent: 或裸 id 返回其上级；
  吃 ``org:`` 返回部门负责人；吃 ``role:`` 报错；无参＝发起人的上级。
- ``users_with_role(string) → users``：角色直接成员（subjects_in("role:…")）。
- ``has_role(one_user, string) → bool``：谓词（CEL 条件位使用）。
查无一律返回空，不抛异常（None/空传播契约，spec §5.5）；目录未配置报
UnknownFunction——技术故障留给故障单（第 8 章错误二分）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError
from flowork.participants.functions import ParticipantFunctions

if TYPE_CHECKING:
    from flowork.engine.context import RuntimeNodeContext
    from flowork.participants.org_directory import OrgDirectoryLike

__all__ = ["ContribDirectoryMissing", "org_vocabulary"]


class ContribDirectoryMissing(FloworkError):
    """contrib 词汇需要配置组织目录（EngineConfig.org）。"""


def _org_of(org: OrgDirectoryLike | None) -> OrgDirectoryLike:
    if org is None:
        raise ContribDirectoryMissing("contrib 取人词汇需要配置组织目录（EngineConfig.org）")
    return org


def _manager_of(ctx: RuntimeNodeContext, org: OrgDirectoryLike | None, user: str = "") -> list[str]:
    """manager_of(one_user)→one_user：无参＝发起人上级；变量名引用按现状解析。"""
    directory = _org_of(org)
    subject = str(ctx.variables.get(user, user)) if user else ctx.instance.start_user
    if subject.startswith("role:"):
        raise FloworkError(f"manager_of 不接受角色主体：{subject!r}（设计期应报错）")
    hit = directory.manager_of(subject)
    return [hit] if hit else []


def _users_with_role(
    ctx: RuntimeNodeContext, org: OrgDirectoryLike | None, role: str = ""
) -> list[str]:
    """users_with_role(string)→users：角色直接成员。"""
    directory = _org_of(org)
    role_name = str(ctx.variables.get(role, role)) if role else ""
    if not role_name:
        return []
    return list(directory.subjects_in(f"role:{role_name}"))


def _has_role(
    ctx: RuntimeNodeContext, org: OrgDirectoryLike | None, user: str = "", role: str = ""
) -> bool:
    """has_role(one_user, string)→bool：谓词，条件位使用（参与人位不适用）。"""
    directory = _org_of(org)
    subject = str(ctx.variables.get(user, user)) if user else ctx.instance.start_user
    role_name = str(ctx.variables.get(role, role))
    if not role_name:
        return False
    return directory.is_member(subject, f"role:{role_name}")


def org_vocabulary() -> ParticipantFunctions:
    """注册三个 org 词汇（基于 OrgDirectory 四方法端口）。"""
    functions = ParticipantFunctions()
    functions.register("manager_of", _manager_of)
    functions.register("users_with_role", _users_with_role)
    functions.register("has_role", _has_role)
    return functions


_ = Any  # typing 占位
