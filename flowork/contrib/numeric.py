"""数值与字符串词汇包：CEL 标准库散件的 contrib 补充。

celpy 0.5.0 没有全局 ``abs/max/min/split/sort/sum``（只有列表方法 ``.min()``
``.reduce()``）——审批域条件的四类主战场（标量/字符串/时间差/集合谓词）之外
的偶发散件由本包补齐。内核保持零内置（「内核只保语法、词汇归宿主」哲学，
spec §9），宿主一行挂上::

    from flowork import contrib
    config = EngineConfig(expression_functions=contrib.numeric_vocabulary())

可与组织词汇并用（两包 ``functions()`` 拍平合并即可，见
tests/semantics/test_numeric_vocabulary.py）。语义约定：

- ``split(string, string) → list``：按分隔符拆分（分隔符为空返回单元素列表）；
- ``abs(number) → number``：绝对值；
- ``max(list) → number`` / ``min(list) → number``：最值；**空列表返回 null**
  （None 传播，比较位将按统一错误语义处理）；
- ``sort(list) → list``：升序（数值或同质字符串，混排报错）；
- ``sum(list) → number``：求和；**空列表返回 0**（加法单位元）。

类型不符抛 FloworkError → FormulaError → 技术故障（挂起+故障单），
与 ``manager_of`` 的 ``role:`` 守卫同一模式。注册全局 ``min`` 不遮蔽
celpy 的 ``[...].min()`` 方法形态（两者签名同为单列表入参，实测共存）。
"""

from __future__ import annotations

from typing import Any

from flowork.api import ExpressionFunction, ExpressionFunctions, FloworkError, FunctionSignature
from flowork.contrib._registry import FixedFunctions

__all__ = ["numeric_vocabulary"]

# 兼容声明（词汇包；openface-plugin-design §4）：依赖的门面契约版本；
# tests/test_plugin_compat.py 断言 ≤ flowork.api.API_VERSION。
REQUIRES_FLOWORK_API = "1"


def _numeric(value: Any, name: str) -> int | float:
    """数值守卫：BoolType 是 int 子类，先拒布尔再收数值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FloworkError(f"{name} 需要数值入参，实际类型 {type(value).__name__}")
    return value


def _items(value: Any, name: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise FloworkError(f"{name} 需要列表入参，实际类型 {type(value).__name__}")
    return list(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise FloworkError(f"{name} 需要字符串入参，实际类型 {type(value).__name__}")
    return value


def numeric_vocabulary() -> ExpressionFunctions:
    """六个数值/字符串词汇的 CEL 注册面（纯函数，无状态无副作用）。"""
    from celpy.adapter import json_to_cel

    def split(text: str, sep: str) -> Any:
        source, divider = _text(text, "split"), _text(sep, "split")
        parts = [source] if not divider else source.split(divider)
        return json_to_cel(parts)

    def abs_of(value: Any) -> Any:
        return abs(_numeric(value, "abs"))

    def max_of(values: Any) -> Any:
        items = [_numeric(v, "max") for v in _items(values, "max")]
        return max(items) if items else None

    def min_of(values: Any) -> Any:
        items = [_numeric(v, "min") for v in _items(values, "min")]
        return min(items) if items else None

    def sort_of(values: Any) -> Any:
        items = _items(values, "sort")
        numeric = [v for v in items if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if numeric and len(numeric) != len(items):
            raise FloworkError("sort 列表元素须同为数值或同为字符串")
        try:
            return json_to_cel(sorted(items))
        except TypeError as exc:
            raise FloworkError(f"sort 列表元素不可比较：{exc}") from exc

    def sum_of(values: Any) -> Any:
        return sum((_numeric(v, "sum") for v in _items(values, "sum")), 0)

    return FixedFunctions(
        {
            "split": ExpressionFunction(
                FunctionSignature(("string", "string"), "list", label="拆分字符串"), split
            ),
            "abs": ExpressionFunction(
                FunctionSignature(("number",), "number", label="绝对值"), abs_of
            ),
            "max": ExpressionFunction(
                FunctionSignature(("list",), "number", label="列表最大值"), max_of
            ),
            "min": ExpressionFunction(
                FunctionSignature(("list",), "number", label="列表最小值"), min_of
            ),
            "sort": ExpressionFunction(
                FunctionSignature(("list",), "list", label="列表排序"), sort_of
            ),
            "sum": ExpressionFunction(
                FunctionSignature(("list",), "number", label="列表求和"), sum_of
            ),
        }
    )
