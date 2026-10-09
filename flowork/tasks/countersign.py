"""会签出口判定：CEL 布尔公式（位点统一收官，kernel-v2 §4.1/§4.2）。

approve_exit / disapprove_exit 为 **CEL 公式**，求值上下文注入 ``task.*``
会签计数快照（item_count / approve_count / disapprove_count，spec C.4 位点
相关形状）与实例变量 + ``initiator``；求值错按错误二分包 EngineExecutionError
（挂起+故障单，§8）。旧专用语法等价翻译（整数乘法，百分比 ceil 精确等价）：

- ``all``      → ``task.approve_count >= task.item_count``
- ``any``      → ``task.approve_count >= 1``
- ``>=N``      → ``task.approve_count >= N``
- ``>=N%``     → ``task.approve_count * 100 >= task.item_count * N``

``expr`` 缺省（None/空）＝该侧全票：side=approve 全员同意、side=disapprove
全员否决（缺省语义与专用语法时代一致，不强制写公式）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal

from flowork.expr.cel import FormulaError, evaluate_formula
from flowork.expr.context import expression_context

__all__ = ["exit_reached"]

Side = Literal["approve", "disapprove"]


def exit_reached(
    expr: str | None,
    *,
    side: Side,
    item_count: int,
    approve_count: int,
    disapprove_count: int,
    variables: Mapping[str, Any] | None = None,
    start_user: str | None = None,
    functions: Mapping[str, Callable[..., object]] | None = None,
    instance: Mapping[str, Any] | None = None,
) -> bool:
    """求值会签出口公式；expr 缺省＝该侧全票（task.* = 三计数快照）。

    ``instance`` 全局事实可选注入（variables 已携带时免传——anticipate 经
    evaluation_variables 自动带上）。
    """
    if not expr:
        done = approve_count if side == "approve" else disapprove_count
        return done >= item_count
    from flowork.engine.kernel import EngineExecutionError

    context = expression_context(
        variables or {},
        start_user=start_user,
        task={
            "item_count": item_count,
            "approve_count": approve_count,
            "disapprove_count": disapprove_count,
        },
        instance=instance,
    )
    try:
        return evaluate_formula(expr, context, functions=functions)
    except FormulaError as exc:
        raise EngineExecutionError(f"会签出口公式求值失败（{expr!r}）：{exc}") from exc
