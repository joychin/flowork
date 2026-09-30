"""条件求值测试：操作符矩阵、组间 OR / 组内 AND、错误路径。"""

from __future__ import annotations

import pytest

from flowork.definitions.flow import ConditionItem, ConditionOperator, FlowCondition
from flowork.errors import ConditionError, FormulaNotSupported, VariableNotDefined
from flowork.expr.conditions import (
    evaluate_condition,
    evaluate_groups,
    evaluate_item,
    resolve_path,
)

_UNSET = object()


def item(left: str, op: str, right=_UNSET) -> ConditionItem:
    if right is _UNSET:  # 单目操作符
        return ConditionItem(left=left, operator=ConditionOperator(op))
    return ConditionItem(left=left, operator=ConditionOperator(op), right=right)


@pytest.mark.parametrize(
    ("condition", "variables", "expected"),
    [
        (item("x", "eq", 1), {"x": 1}, True),
        (item("x", "eq", 1), {"x": 2}, False),
        (item("x", "ne", 1), {"x": 2}, True),
        (item("x", "gt", 10), {"x": 11}, True),
        (item("x", "ge", 10), {"x": 10}, True),
        (item("x", "lt", 10), {"x": 9}, True),
        (item("x", "le", 10), {"x": 10}, True),
        (item("x", "in", ["a", "b"]), {"x": "a"}, True),
        (item("x", "not_in", ["a", "b"]), {"x": "c"}, True),
        (item("x", "contains", "pay"), {"x": "payment"}, True),
        (item("tags", "contains", "vip"), {"tags": ["vip", "new"]}, True),
        (item("x", "starts_with", "PRJ"), {"x": "PRJ-42"}, True),
        (item("x", "is_empty"), {"x": ""}, True),
        (item("x", "is_empty"), {"x": None}, True),
        (item("x", "is_not_empty"), {"x": "ok"}, True),
        (item("a.b", "eq", 2), {"a": {"b": 2}}, True),  # 点号路径
    ],
)
def test_operator_matrix(condition, variables, expected):
    assert evaluate_item(condition, variables) is expected


def test_group_semantics_and_or():
    variables = {"x": 5, "y": 0}
    both_true = [[item("x", "gt", 1)], [item("y", "gt", 1)]]
    assert evaluate_groups(both_true, variables) is True  # 组间 OR
    within_group = [[item("x", "gt", 1), item("x", "lt", 10)]]
    assert evaluate_groups(within_group, variables) is True  # 组内 AND
    mixed = [[item("x", "gt", 1), item("y", "gt", 1)]]  # 组内 AND 不满足
    assert evaluate_groups(mixed, variables) is False
    assert evaluate_groups([], variables) is False


def test_missing_variable_raises():
    with pytest.raises(VariableNotDefined):
        evaluate_item(item("ghost", "eq", 1), {})


def test_numeric_compare_type_guard():
    with pytest.raises(ConditionError):
        evaluate_item(item("x", "gt", 1), {"x": "not-a-number"})


def test_formula_not_supported_yet():
    condition = FlowCondition(formula="amount > 100")
    with pytest.raises(FormulaNotSupported):
        evaluate_condition(condition, {"amount": 200})


def test_condition_prefers_groups():
    condition = FlowCondition(groups=[[item("x", "eq", 1)]], formula="never_evaluated")
    assert evaluate_condition(condition, {"x": 1}) is True


def test_resolve_path_nested():
    assert resolve_path({"a": {"b": {"c": 7}}}, "a.b.c") == 7
