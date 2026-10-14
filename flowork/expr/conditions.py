"""条件求值（单轨）：CEL 公式经 ``expr.cel`` 求值。

结构化条件组（groups）已退役（零兼容，存量已迁移）。求值错（FormulaError）
由 engine.routing 统一包成 EngineExecutionError（技术故障：挂起 + 故障单）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from flowork.errors import ConditionError

__all__ = ["ConditionError", "evaluate_condition"]


def evaluate_condition(
    condition: str,
    variables: Mapping[str, Any],
    functions: Mapping[str, Callable[..., object]] | None = None,
) -> bool:
    """求值路由条件（CEL 公式）。"""
    from flowork.expr.cel import evaluate_formula

    return evaluate_formula(condition, dict(variables), functions=functions)
