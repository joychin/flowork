"""contrib 内部共享件：固定注册表的 ExpressionFunctions 实现。"""

from __future__ import annotations

from collections.abc import Mapping

from flowork.spi.ports import ExpressionFunction

__all__ = ["FixedFunctions"]


class FixedFunctions:
    """固定注册表的 ExpressionFunctions 实现（各词汇包的返回值）。"""

    def __init__(self, table: Mapping[str, ExpressionFunction]) -> None:
        self._table = dict(table)

    def functions(self) -> Mapping[str, ExpressionFunction]:
        return self._table
