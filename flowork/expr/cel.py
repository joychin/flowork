"""CEL 公式求值（kernel-v2 §4.3）：``FlowCondition.formula`` 的运行时实现。

cel-python（模块名 ``celpy``）执行，P0-1 决策记录的落地缓解四件套：
**2KB 限长 / 二元运算链 ≤256 项 / 变量序列化 ≤1MB / 全异常归一 FormulaError**
（celpy 无进程内硬超时——非图灵完备 + 三护栏间接约束，升级路径为子进程隔离）。
静态检查原料由 :func:`collect_references` 提供（标识符 + 函数名→参数个数），
validator 侧对照 input 契约与 ExpressionFunctions 注册表使用。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, cast

import celpy
from celpy.adapter import json_to_cel

from flowork.definitions.flow import MAX_FORMULA_LENGTH
from flowork.errors import ConditionError

__all__ = [
    "CEL_BUILTIN_FUNCTIONS",
    "MAX_BINARY_OPS",
    "MAX_VARIABLE_BYTES",
    "FormulaError",
    "collect_references",
    "evaluate_formula",
]

# 二元运算链项数上限：PoC 实测 350 项（1747B）触发 celpy 递归悬崖，256 留余量。
MAX_BINARY_OPS = 255  # 每个二元节点计一次运算；255 次 ⇒ 256 项

# 变量序列化上限（间接约束 comprehension 可遍历的集合规模）。
MAX_VARIABLE_BYTES = 1024 * 1024

# CEL 内建全局函数（标准库 + comprehension 宏）：静态检查免检名单——
# 未注册又不在名单内的函数调用才报「未知函数」（validator 侧使用）。
CEL_BUILTIN_FUNCTIONS = frozenset(
    {
        "size",
        "has",
        "starts",
        "ends",
        "matches",
        "string",
        "double",
        "int",
        "uint",
        "bytes",
        "duration",
        "timestamp",
        "dyn",
        "type",
        "all",
        "exists",
        "exists_one",
        "map",
        "filter",
    }
)

# comprehension 宏（方法或函数形态）：第一个参数是绑定变量，不算标识符引用。
_COMPREHENSION_MACROS = frozenset({"all", "exists", "exists_one", "map", "filter"})

# 计入链项护栏的二元运算符（Token 值精确匹配；字面量 Token 带引号不会撞上）。
# lark 的 addition/multiplication 是每个操作数都套一层的前缀级联规则，不能数节点。
_BINARY_OPERATORS = frozenset({"+", "-", "*", "/", "%", "&&", "||"})


class FormulaError(ConditionError):
    """公式求值失败（语法/安全/类型/护栏）。"""


def _binary_op_count(formula: str) -> int:
    """数字符串里的二元运算符（跳过字符串字面量；lark 把运算符揉进规则形状，不可数节点）。"""
    count = 0
    quote: str | None = None
    i = 0
    while i < len(formula):
        ch = formula[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
        elif formula[i : i + 2] in ("&&", "||"):
            count += 1
            i += 2
            continue
        elif ch in "+-*/%":
            count += 1
        i += 1
    return count


def _parse(formula: str) -> celpy.Expression[Any]:
    """编译并过护栏：空式/超长/链项超限在 compile 前统一拦截（护住 lark 自身递归）。"""
    formula = formula.strip()
    if not formula:
        raise FormulaError("公式为空")
    if len(formula) > MAX_FORMULA_LENGTH:
        raise FormulaError(f"公式长度超限（>{MAX_FORMULA_LENGTH}）")
    if _binary_op_count(formula) > MAX_BINARY_OPS:
        raise FormulaError(f"二元运算链项数超限（>{MAX_BINARY_OPS}）：{formula[:64]!r}…")
    env = celpy.Environment()
    try:
        return env.compile(formula)
    except FormulaError:
        raise
    except Exception as exc:  # CELParseError 及 lark 层异常
        raise FormulaError(f"公式语法错误：{formula!r}（{exc}）") from exc


def evaluate_formula(
    formula: str,
    variables: Mapping[str, Any],
    functions: Mapping[str, Callable[..., object]] | None = None,
) -> bool:
    """在 CEL 求值公式，返回布尔结果（celtypes.BoolType 收敛为 Python bool）。

    Args:
        formula: CEL 表达式（``&& / || / ! / true / false / null / in [...] / .size()``）。
        variables: 变量上下文（JSON 兼容；入参经 json_to_cel 支持字段选择）。
        functions: 自定义函数注册表（宿主经 ExpressionFunctions 端口拍平后传入）。
    """
    tree = _parse(formula)
    payload = dict(variables)
    try:
        size = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception as exc:
        raise FormulaError(f"变量序列化失败：{exc}") from exc
    if size > MAX_VARIABLE_BYTES:
        raise FormulaError(f"变量序列化超限（{size} > {MAX_VARIABLE_BYTES}B）")
    activation = {key: json_to_cel(value) for key, value in payload.items()}
    env = celpy.Environment()
    program = env.program(tree, functions=cast(Any, dict(functions)) if functions else None)
    try:
        result = program.evaluate(activation)
    except FormulaError:
        raise
    except Exception as exc:  # CELEvalError / RecursionError 兜底归一
        raise FormulaError(f"公式求值错误：{formula!r}（{exc}）") from exc
    return bool(result)


def collect_references(formula: str) -> tuple[set[str], dict[str, set[int]]]:
    """静态收集公式引用：标识符集合 + 函数名→参数个数集合。

    - 标识符＝变量引用（member 链取根：``input.amount`` 只收 ``input``）；
    - 函数＝``ident_arg`` 全局调用（``manager_of(x)``）；``.size()`` 等方法不计；
    - comprehension 宏（``list.exists(x, …)``）的绑定变量不算标识符。
    供 validator 静态检查（对照已声明变量与函数注册表签名）。
    """
    tree = _parse(formula)
    identifiers: set[str] = set()
    functions: dict[str, set[int]] = {}
    bound: set[str] = set()

    def visit(node: Any) -> None:
        data = getattr(node, "data", None)
        if data is None:  # Token（字面量/字段名/运算符）
            return
        children = node.children
        if data == "ident":
            identifiers.add(str(children[0]))
            return
        if data == "ident_arg":  # 全局函数调用：名 + 可选 exprlist
            name = str(children[0])
            args = next((c for c in children if getattr(c, "data", None) == "exprlist"), None)
            functions.setdefault(name, set()).add(0 if args is None else len(args.children))
            if name in _COMPREHENSION_MACROS and args is not None:
                bound.update(_binding_names(args.children))
                for expr in list(args.children)[1:]:
                    visit(expr)
            elif args is not None:
                for expr in args.children:
                    visit(expr)
            return
        if data == "member_dot_arg":  # 方法调用：base + 方法名 + 可选 exprlist
            method = str(children[1])
            args = next((c for c in children if getattr(c, "data", None) == "exprlist"), None)
            if method in _COMPREHENSION_MACROS and args is not None:
                bound.update(_binding_names(args.children))
                exprs = list(args.children)[1:]
            else:
                exprs = list(args.children) if args is not None else []
            visit(children[0])
            for expr in exprs:
                visit(expr)
            return
        for child in children:
            visit(child)

    def _binding_names(arg_exprs: list[Any]) -> set[str]:
        """comprehension 宏第一个实参的标识符（绑定名，可能是逗号分隔的多绑定）。"""
        names: set[str] = set()
        for expr in arg_exprs[:1]:
            for sub in expr.iter_subtrees():
                if sub.data == "ident":
                    names.add(str(sub.children[0]))
        return names

    visit(tree)
    return identifiers - bound, functions
