"""contrib 官方插件包：词汇插件 + web 传输面（rest/mcp 进驻后同列）。

本包是「官方插件」而非内核——双向铁律（import-linter 红灯，见 pyproject
[tool.importlinter]）：内核包禁止 import 本包；本包（含 web 传输面）只准经
``flowork.api`` 公开门面使用内核。词汇部分纯基于 OrgDirectory 端口实现，
内核注册表出厂为空。宿主用法::

    from flowork import contrib
    config = EngineConfig(
        org=org,
        expression_functions=contrib.cel_vocabulary(org),  # 全表达式位共用（spec §4.2）
    )

位点统一（原「两处注册面」已收敛为一）：条件位与取人位同一套词汇——
- ``manager_of(one_user) → one_user``：吃 user:/agent: 或裸 id 返回其上级；
  吃 ``org:`` 返回部门负责人；吃 ``role:`` 报错；查无返回 null（None 传播）。
- ``users_with_role(string) → users``：角色直接成员（subjects_in("role:…")）。
- ``has_role(one_user, string) → bool``：谓词（条件位使用，取人位会被拒）。
查无一律返回 None/空，不抛异常（None/空传播契约，spec §5.5）；目录未配置报
ContribDirectoryMissing——技术故障留给故障单（第 8 章错误二分）。
CEL 面的函数是**纯读闭包**（无 ctx、无副作用——路由决策只属于引擎+条件求值）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.api import ExpressionFunction, ExpressionFunctions, FloworkError, FunctionSignature
from flowork.contrib._registry import FixedFunctions
from flowork.contrib.numeric import numeric_vocabulary

if TYPE_CHECKING:
    from flowork.api import OrgDirectoryLike

__all__ = ["ContribDirectoryMissing", "cel_vocabulary", "numeric_vocabulary"]


class ContribDirectoryMissing(FloworkError):
    """contrib 词汇需要配置组织目录（EngineConfig.org）。"""


def cel_vocabulary(org: OrgDirectoryLike) -> ExpressionFunctions:
    """三个 org 词汇的 CEL 注册面（闭包持有目录端口）。

    函数纯读：只调 OrgDirectory 四方法，无 ctx、不写变量、无副作用；
    查无一律返回 null/空（None/空传播契约，spec §5.5）。
    """

    def manager_of(user: str) -> str | None:
        if user.startswith("role:"):
            raise FloworkError(f"manager_of 不接受角色主体：{user!r}（设计期应报错）")
        return org.manager_of(user)

    def users_with_role(role: str) -> list[str]:
        return list(org.subjects_in(f"role:{role}")) if role else []

    def has_role(user: str, role: str) -> bool:
        return bool(role) and org.is_member(user, f"role:{role}")

    return FixedFunctions(
        {
            "manager_of": ExpressionFunction(
                FunctionSignature(("one_user",), "one_user", label="取上级"), manager_of
            ),
            "users_with_role": ExpressionFunction(
                FunctionSignature(("string",), "users", label="按角色取人"), users_with_role
            ),
            "has_role": ExpressionFunction(
                FunctionSignature(("one_user", "string"), "bool", label="判断角色成员"), has_role
            ),
        }
    )
