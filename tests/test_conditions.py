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


def _exit(expr: str, item: int, done: int) -> bool:
    """会签出口公式求值便捷件：done 计入 approve_count（disapprove 位同理）。"""
    from flowork.tasks.countersign import exit_reached

    return exit_reached(
        expr, side="approve", item_count=item, approve_count=done, disapprove_count=0
    )


def test_exit_formula_ratio_boundaries():
    """会签出口公式（单轨 CEL）：整数乘法与旧 >=N% 的 ceil 语义精确等价。

    ceil(item×N/100) ≤ done ⟺ done×100 ≥ item×N（done 为整数），无浮点无舍入歧义。
    """
    f = "task.approve_count * 100 >= task.item_count * 50"
    assert _exit(f, 2, 1) is True  # 2×50% → 1 票即达
    assert _exit(f, 3, 1) is False  # 3×50% → 需 2 票
    assert _exit(f, 3, 2) is True
    all_f = "task.approve_count >= task.item_count"
    assert _exit(f"{all_f}", 3, 3) is True  # 100% ≡ all
    assert _exit(all_f, 3, 2) is False
    assert _exit("task.approve_count >= 2", 5, 2) is True
    assert _exit("task.approve_count >= 1", 5, 1) is True  # any ≡ >=1
    assert _exit(all_f, 0, 0) is True  # 无应办人视为达成


def test_exit_formula_compound_and_business_variable():
    """DSL 时代写不出的形态：双计数复合条件 + 业务变量参与法定人数（三元）。"""
    from flowork.tasks.countersign import exit_reached

    compound = "task.approve_count >= 2 && task.disapprove_count == 0"
    assert exit_reached(compound, side="approve", item_count=3, approve_count=2, disapprove_count=0)
    assert not exit_reached(
        compound, side="approve", item_count=3, approve_count=2, disapprove_count=1
    )
    assert not exit_reached(
        compound, side="approve", item_count=3, approve_count=1, disapprove_count=0
    )

    quorum = (
        "input.amount < 10000 ? task.approve_count >= 1 : task.approve_count >= task.item_count"
    )
    small = {"input": {"amount": 5000}}
    assert exit_reached(
        quorum, side="approve", item_count=3, approve_count=1, disapprove_count=0, variables=small
    )
    big = {"input": {"amount": 50000}}
    assert not exit_reached(
        quorum, side="approve", item_count=3, approve_count=2, disapprove_count=0, variables=big
    )
    assert exit_reached(
        quorum, side="approve", item_count=3, approve_count=3, disapprove_count=0, variables=big
    )


def test_exit_formula_error_is_technical_fault():
    """出口公式求值错 → EngineExecutionError（挂起+故障单，错误二分一致）。"""
    from flowork.engine.kernel import EngineExecutionError
    from flowork.tasks.countersign import exit_reached

    with pytest.raises(EngineExecutionError, match="会签出口公式求值失败"):
        exit_reached(
            "nosuchvar > 1", side="approve", item_count=3, approve_count=1, disapprove_count=0
        )

    # 缺省全票语义（side 决定用哪个计数）
    assert exit_reached(None, side="approve", item_count=3, approve_count=3, disapprove_count=0)
    assert not exit_reached(None, side="approve", item_count=3, approve_count=2, disapprove_count=1)
    assert exit_reached(None, side="disapprove", item_count=3, approve_count=0, disapprove_count=3)
