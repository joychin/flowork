"""contrib 内部共享件：固定注册表的 ExpressionFunctions 实现 + 返回值抬升。

**返回值抬升（CEL 值域适配）**：函数按 CEL 词汇声明返回
类型（``bool``/``users``/``one_user``…，spec §5.5），Python 侧自然返回值却是
原生 ``bool``/``str``/``list``——而 celpy 的运算符重载按 CEL 类型分派，原生
bool 进不了 ``!has_role(...)`` 与 ``cond ? a : b``（实测报
``no matching overload ... <class 'bool'>``，把谓词写进 CEL 组合式即技术故障）。
注册面在边界统一抬升：原生 JSON 值经 ``json_to_cel`` 转 CEL 值；已是 CEL 值的
原样透传（幂等），域外对象原样返回（留给调用方自证，不吞错）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, cast

from celpy import celtypes
from celpy.adapter import json_to_cel

from flowork.api import ExpressionFunction

__all__ = ["FixedFunctions"]

# celpy 的 CEL 值类型：命中即原样透传（BoolType 是 int 子类等——须先判，别被
# json_to_cel 的 isinstance 分支改写语义）。
_CEL_VALUE_TYPES = (
    celtypes.BoolType,
    celtypes.BytesType,
    celtypes.DoubleType,
    celtypes.DurationType,
    celtypes.IntType,
    celtypes.ListType,
    celtypes.MapType,
    celtypes.StringType,
    celtypes.TimestampType,
    celtypes.UintType,
)

# 可抬升的原生 Python 类型（json_to_cel 的定义域）。
_LIFTABLE = (bool, str, int, float, list, tuple, dict, datetime, timedelta)


def _lift(value: Any) -> Any:
    """原生返回值 → CEL 值；CEL 值与域外对象原样透传。"""
    if isinstance(value, _CEL_VALUE_TYPES) or not isinstance(value, _LIFTABLE):
        return value
    # json_to_cel 的运行时定义域含 datetime/timedelta（其类型标注未跟上）。
    return json_to_cel(cast("Any", value))


def _lifting(fn: Callable[..., object]) -> Callable[..., object]:
    """包一层返回值抬升（参数不改：celpy 传入的已是 CEL 值）。"""

    def call(*args: Any, **kwargs: Any) -> Any:
        return _lift(fn(*args, **kwargs))

    return call


class FixedFunctions:
    """固定注册表的 ExpressionFunctions 实现（各词汇包的返回值）。"""

    def __init__(self, table: Mapping[str, ExpressionFunction]) -> None:
        self._table = {
            name: ExpressionFunction(entry.signature, _lifting(entry.fn))
            for name, entry in table.items()
        }

    def functions(self) -> Mapping[str, ExpressionFunction]:
        return self._table
