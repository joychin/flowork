"""会签出口判定：比例表达式求值（纯函数，语义对齐 对标产品 三计数模型）。

表达式语法（approve_exit / disapprove_exit）：
- ``all``       全员同意（默认）
- ``any``       任一人（>=1）
- ``>=N%``      达到 N%（向上取整，如 3 人 50% 需 2 人）
- ``>=N``       至少 N 人
"""

from __future__ import annotations

import re

from flowork.errors import FloworkError

__all__ = ["evaluate_exit", "parse_exit_expr"]


class InvalidExitExpression(FloworkError):
    """出口表达式非法。"""


_RATIO = re.compile(r"^>=(\d+)%$")
_COUNT = re.compile(r"^>=(\d+)$")


def parse_exit_expr(expr: str | None) -> tuple[str, int | None]:
    """解析出口表达式 → (语义, 阈值)。all/any 返回 (语义, None)。"""
    if expr is None or expr.strip() == "" or expr.strip() == "all":
        return "all", None
    token = expr.strip()
    if token == "any":
        return "any", None
    if match := _RATIO.match(token):
        percent = int(match.group(1))
        if not 1 <= percent <= 100:
            raise InvalidExitExpression(f"比例超出范围（1-100）：{expr}")
        return "ratio", percent
    if match := _COUNT.match(token):
        return "count", int(match.group(1))
    raise InvalidExitExpression(f"无法解析出口表达式：{expr!r}（支持 all/any/>=N%/>=N）")


def evaluate_exit(expr: str | None, item_count: int, done_count: int) -> bool:
    """判定出口是否达成。done_count 为同意数（或否决数，视表达式位置）。"""
    kind, threshold = parse_exit_expr(expr)
    if item_count <= 0:
        return True  # 无应办人视为达成（如全员自动通过）
    if kind == "all":
        return done_count >= item_count
    if kind == "any":
        return done_count >= 1
    if kind == "ratio":
        needed = -(-item_count * (threshold or 0) // 100)  # 向上取整
        return done_count >= needed
    return done_count >= (threshold or 0)
