"""公式沙箱求值（asteval 白名单）：FlowCondition.formula 的运行时实现。

安全策略：符号表仅保留白名单（禁 import/exec/循环结构等由 asteval 默认禁止）、
长度上限；结果必须为布尔值，否则 FormulaError。
"""

from __future__ import annotations

from typing import Any

from asteval import Interpreter

from flowork.errors import ConditionError

__all__ = ["MAX_FORMULA_LENGTH", "FormulaError", "evaluate_formula"]

MAX_FORMULA_LENGTH = 4096

_ALLOWED_SYMBOLS: frozenset[str] = frozenset(
    {
        "abs",
        "min",
        "max",
        "round",
        "len",
        "sum",
        "str",
        "int",
        "float",
        "bool",
        "True",
        "False",
        "None",
    }
)


class FormulaError(ConditionError):
    """公式求值失败（语法/安全/类型）。"""


def evaluate_formula(formula: str, variables: dict[str, Any]) -> bool:
    """在白名单沙箱中求值公式，返回布尔结果。

    Examples:
        >>> evaluate_formula("amount > 1000 and level == 'L2'", {"amount": 2000, "level": "L2"})
        True
    """
    formula = formula.strip()
    if not formula:
        raise FormulaError("公式为空")
    if len(formula) > MAX_FORMULA_LENGTH:
        raise FormulaError(f"公式长度超限（>{MAX_FORMULA_LENGTH}）")
    interpreter = Interpreter()
    for name in list(interpreter.symtable):
        if name not in _ALLOWED_SYMBOLS:
            interpreter.symtable.pop(name, None)
    for name, value in variables.items():
        if not name.isidentifier():
            raise FormulaError(f"变量名非法：{name!r}")
        interpreter.symtable[name] = value
    result = interpreter(formula)
    if interpreter.error:
        raise FormulaError(f"公式语法/执行错误：{formula!r}（{interpreter.error[0]}）")
    if not isinstance(result, bool):
        raise FormulaError(f"公式结果必须是布尔值，得到：{result!r}")
    return result
