"""contrib 便利包（v2.1，spec §5.5/C.4）：可选的取人词汇，一行挂上。

纯基于 OrgDirectory 四方法端口实现，内核注册表出厂为空——本包是「官方插件」，
内核（engine/participants/expr/definitions 等模块）import 本包即违反边界铁律
（import-linter 红灯，见 pyproject [tool.importlinter]）。宿主用法::

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

from flowork.contrib._registry import FixedFunctions
from flowork.contrib.numeric import numeric_vocabulary
from flowork.errors import FloworkError
from flowork.spi.ports import ExpressionFunction, ExpressionFunctions, FunctionSignature

if TYPE_CHECKING:
    from flowork.participants.org_directory import OrgDirectoryLike

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
