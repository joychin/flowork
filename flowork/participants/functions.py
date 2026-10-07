"""取人函数注册表：参与者表达式 ``name(args)`` 的求值（v2.1：出厂为空）。

v2.1 定稿（spec §5.5）：内核不内置任何取人词汇——组→人的函数全部由宿主经
注册表挂入（官方便利包见 ``flowork.contrib.org_vocabulary``，一行挂上）。
内核只保「语法」：表达式解析、注册契约、查无报错。
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
    """取人函数注册表：name → callable(ctx, org, *args)。

    出厂为空（v2.1）；宿主自行注册或挂 ``flowork.contrib.org_vocabulary()``。
    返回形态：参与人位 list[str]；谓词位（如 has_role）bool——由使用位置约定。
    """

    _registry: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def register(self, name: str, fn: Callable[..., Any], *, replace: bool = False) -> None:
        if name in self._registry and not replace:
            raise ValueError(f"取人函数 {name!r} 已注册（replace=True 可覆盖）")
        self._registry[name] = fn

    def call(
        self, name: str, ctx: RuntimeNodeContext, org: OrgDirectoryLike | None, args: list[str]
    ) -> Any:
        if name not in self._registry:
            known = ", ".join(self._registry)
            raise UnknownFunction(f"未注册的取人函数：{name!r}（已注册：{known}）")
        return self._registry[name](ctx, org, *args)

    def names(self) -> tuple[str, ...]:
        return tuple(self._registry)
