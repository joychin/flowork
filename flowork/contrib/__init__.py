"""contrib 官方插件包：词汇插件 + web 传输面（rest/mcp 进驻后同列）。

本包是「官方插件」而非内核——双向铁律（import-linter 红灯，见 pyproject
[tool.importlinter]）：内核包禁止 import 本包；本包（含 web 传输面）只准经
``flowork.api`` 公开门面使用内核。词汇部分纯基于 OrgDirectory 端口实现，
内核注册表出厂为空。宿主用法::

    from flowork import contrib
    config = EngineConfig(
        org=org,
        expression_functions=contrib.cel_vocabulary(org),  # 全表达式位共用
    )

位点统一（原「两处注册面」已收敛为一）：条件位与取人位同一套词汇——
- ``manager_of(one_user) → one_user``：返回其上级。人 → 主属部门主管；
  吃 ``org:`` 返回部门负责人；吃 ``role:`` 报错；查无返回 null（None 传播）。
- ``users_with_role(string) → users``：角色直接成员（subjects_in("role:…")）。
- ``users_in_org(string) → users``：部门直接成员（subjects_in("org:…")）——
  取人位按部门展开的唯一写法（组引用只属于候选槽）。
- ``has_role(one_user, string) → bool``：谓词（条件位使用，取人位会被拒）。
查无一律返回 None/空，不抛异常（None/空传播契约）；表达式求值期目录自身
抛错＝技术故障（挂起+故障单，错误二分）。
CEL 面的函数是**纯读闭包**（无 ctx、无副作用——路由决策只属于引擎+条件求值）。

返回值经注册面统一抬升到 CEL 值域（``_registry._lift``）：宿主写函数时按
FunctionSignature 声明类型、返回 Python 原生值即可——``bool`` 经抬升才能在
``!has_role(...)`` 与 ``cond ? a : b`` 里参与 CEL 运算符重载（celpy 按 CEL
类型分派，原生 bool 会被判无匹配重载而报错）。**参数侧反之**：celpy 传进来的
已是 CEL 值，函数收到的事实缺键仍是 Python ``None``——把 null 当人喂给
``manager_of`` 是技术故障，CEL 里必须先用 ``x != null ? ... : ...`` 判空。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.api import ExpressionFunction, ExpressionFunctions, FloworkError, FunctionSignature
from flowork.contrib._registry import FixedFunctions
from flowork.contrib.numeric import numeric_vocabulary

if TYPE_CHECKING:
    from flowork.api import OrgDirectoryLike

__all__ = ["cel_vocabulary", "numeric_vocabulary"]

# 兼容声明（词汇包 cel_vocabulary/numeric_vocabulary）：
# 依赖的门面契约版本；tests/test_plugin_compat.py 断言 ≤ flowork.api.API_VERSION。
REQUIRES_FLOWORK_API = "1"


def cel_vocabulary(org: OrgDirectoryLike) -> ExpressionFunctions:
    """四个 org 词汇的 CEL 注册面（闭包持有目录端口）。

    函数纯读：只调 OrgDirectory 四方法，无 ctx、不写变量、无副作用；
    查无一律返回 null/空（None/空传播契约）。
    """

    def manager_of(user: str) -> str | None:
        if user.startswith("role:"):
            raise FloworkError(f"manager_of 不接受角色主体：{user!r}（设计期应报错）")
        return org.manager_of(user)

    def users_with_role(role: str) -> list[str]:
        return list(org.subjects_in(f"role:{role}")) if role else []

    def users_in_org(department: str) -> list[str]:
        return list(org.subjects_in(f"org:{department}")) if department else []

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
            "users_in_org": ExpressionFunction(
                FunctionSignature(("string",), "users", label="按部门取人"), users_in_org
            ),
            "has_role": ExpressionFunction(
                FunctionSignature(("one_user", "string"), "bool", label="判断角色成员"), has_role
            ),
        }
    )
