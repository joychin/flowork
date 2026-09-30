"""条件求值：结构化条件组（组间 OR、组内 AND）。

公式（formula）求值 M2 提供沙箱实现；当前引用公式抛 FormulaNotSupported。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flowork.definitions.flow import ConditionItem, FlowCondition
from flowork.errors import ConditionError, FormulaNotSupported, VariableNotDefined

__all__ = ["evaluate_condition", "evaluate_groups", "evaluate_item", "resolve_path"]


def resolve_path(variables: Mapping[str, Any], path: str) -> Any:
    """按点号路径取变量值；路径不存在抛 VariableNotDefined。"""
    value: Any = variables
    walked: list[str] = []
    for segment in path.split("."):
        walked.append(segment)
        if isinstance(value, Mapping) and segment in value:
            value = value[segment]
        else:
            raise VariableNotDefined(".".join(walked))
    return value


def _as_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConditionError(f"比较操作要求数值，得到：{value!r}")
    return float(value)


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def evaluate_item(item: ConditionItem, variables: Mapping[str, Any]) -> bool:
    """求值单个条件项。"""
    left = resolve_path(variables, item.left)
    op = item.operator
    if op in ("gt", "ge", "lt", "le"):
        try:
            right = _as_number(item.right)
        except ConditionError as exc:
            raise ConditionError(f"条件 {item.left} {op} 的右值非法：{exc}") from exc
        lv = _as_number(left)
        if op == "gt":
            return lv > right
        if op == "ge":
            return lv >= right
        if op == "lt":
            return lv < right
        return lv <= right
    if op == "eq":
        return bool(left == item.right)
    if op == "ne":
        return bool(left != item.right)
    if op in ("in", "not_in"):
        if not isinstance(item.right, list):
            raise ConditionError(f"条件 {item.left} {op} 要求右值为列表")
        result = bool(left in item.right)
        return result if op == "in" else not result
    if op == "contains":
        if isinstance(left, str):
            return str(item.right) in left
        if isinstance(left, list):
            return bool(item.right in left)
        raise ConditionError(f"contains 要求左值为字符串或列表，得到：{left!r}")
    if op == "starts_with":
        if not isinstance(left, str):
            raise ConditionError(f"starts_with 要求左值为字符串，得到：{left!r}")
        return left.startswith(str(item.right))
    if op == "is_empty":
        return _is_empty(left)
    if op == "is_not_empty":
        return not _is_empty(left)
    raise ConditionError(f"未知操作符：{op}")  # pragma: no cover - StrEnum 已限制


def evaluate_groups(groups: list[list[ConditionItem]], variables: Mapping[str, Any]) -> bool:
    """组间 OR、组内 AND；空组列表视为 False（无条件不成立）。"""
    if not groups:
        return False
    return any(all(evaluate_item(item, variables) for item in group) for group in groups)


def evaluate_condition(condition: FlowCondition, variables: Mapping[str, Any]) -> bool:
    """求值路由条件：结构化组优先；公式 M2 前显式报未支持。"""
    if condition.groups:
        return evaluate_groups(condition.groups, variables)
    if condition.formula:
        raise FormulaNotSupported(
            f"公式条件 {condition.formula!r} 将在 M2 启用（沙箱求值），当前请使用结构化条件组"
        )
    return False
