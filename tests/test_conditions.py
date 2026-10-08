"""条件求值测试（单轨 CEL）：公式语义、错误路径、会签出口。"""

from __future__ import annotations

import pytest

from flowork.errors import ConditionError
from flowork.expr.conditions import evaluate_condition


@pytest.mark.parametrize(
    ("condition", "variables", "expected"),
    [
        ("x > 1", {"x": 2}, True),
        ("x > 1", {"x": 1}, False),
        ('x == "L2"', {"x": "L2"}, True),
        ("x in [1, 2, 3]", {"x": 2}, True),
        ("items.size() > 1", {"items": [1, 2]}, True),
        ("input.amount >= 300 && input.urgent", {"input": {"amount": 500, "urgent": True}}, True),
        ("x == null", {"x": None}, True),
    ],
)
def test_cel_condition_semantics(condition, variables, expected):
    assert evaluate_condition(condition, variables) is expected


def test_cel_condition_errors_normalized():
    with pytest.raises(ConditionError):
        evaluate_condition("ghost > 1", {})  # 未提供变量 → 求值错归一 FormulaError
    with pytest.raises(ConditionError):
        evaluate_condition("x >", {"x": 1})  # 语法错误


def test_exit_expression_ratio_boundaries():
    """会签出口比例边界：50% 的 2 人=1 票、3 人=2 票（向上取整）；100%=all。"""
    from flowork.tasks.countersign import evaluate_exit, parse_exit_expr

    assert evaluate_exit(">=50%", 2, 1) is True  # 2×50% → 1 票即达
    assert evaluate_exit(">=50%", 3, 1) is False  # 3×50% → 需 2 票
    assert evaluate_exit(">=50%", 3, 2) is True
    assert evaluate_exit(">=100%", 3, 3) is True
    assert evaluate_exit(">=100%", 3, 2) is False
    assert evaluate_exit(">=2", 5, 2) is True
    assert evaluate_exit("any", 5, 1) is True
    assert evaluate_exit(None, 0, 0) is True  # 无应办人
    assert parse_exit_expr(">=50%") == ("ratio", 50)
    assert parse_exit_expr("all") == ("all", None)
