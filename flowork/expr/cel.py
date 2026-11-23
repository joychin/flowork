"""CEL 公式求值：全表达式位的运行时实现。

cel-python（模块名 ``celpy``）执行的落地缓解四件套：
**2KB 限长 / 二元运算链 ≤256 项 / 变量序列化 ≤1MB / 全异常归一 FormulaError**
（celpy 无进程内硬超时——非图灵完备 + 三护栏间接约束，升级路径为子进程隔离）。
两个入口：``evaluate_formula``（条件位，bool 收敛）与 ``evaluate_formula_value``
（值位：取人/传阅/改派/映射/试算，原样返回）。
静态检查原料由 :func:`collect_references` 提供（标识符 + 函数名→参数个数），
validator 侧对照 input 契约与 ExpressionFunctions 注册表使用。
"""

from __future__ import annotations

import json
import re
import sys
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
    "evaluate_formula_value",
    "references_identifier",
    "root_call",
]

# 二元运算链项数上限：PoC 实测 350 项（1747B）触发 celpy 递归悬崖，256 留余量。
MAX_BINARY_OPS = 255  # 每个二元节点计一次运算；255 次 ⇒ 256 项

# 变量序列化上限（间接约束 comprehension 可遍历的集合规模）。
MAX_VARIABLE_BYTES = 1024 * 1024

# CEL 内建全局函数（celpy base_functions + comprehension 宏）：静态检查免检名单——
# 未注册又不在名单内的函数调用才报「未知函数」（validator 侧使用）。
# 注意 celpy 的真名是 startsWith/endsWith（camelCase），contains/bool/list/null_type
# 亦是全局可调内建。
CEL_BUILTIN_FUNCTIONS = frozenset(
    {
        "size",
        "has",
        "startsWith",
        "endsWith",
        "contains",
        "matches",
        "string",
        "double",
        "int",
        "uint",
        "bytes",
        "bool",
        "list",
        "null_type",
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

# activation 合法键＝CEL 标识符语法（ASCII）；中文等变量名不可被公式引用，装载即拒收。
_CEL_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def _evaluate(
    formula: str,
    variables: Mapping[str, Any],
    functions: Mapping[str, Callable[..., object]] | None = None,
) -> Any:
    """共享求值核：护栏全过，返回原值（bool 收敛由条件位变体自理）。"""
    tree = _parse(formula)
    payload = dict(variables)
    try:
        size = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception as exc:
        raise FormulaError(f"变量序列化失败：{exc}") from exc
    if size > MAX_VARIABLE_BYTES:
        raise FormulaError(f"变量序列化超限（{size} > {MAX_VARIABLE_BYTES}B）")
    # 非 ASCII 键（如中文变量名）在 CEL 标识符语法内本就不可引用，装进 activation
    # 反而会被 celpy 的 NameContainer 拒收（Invalid name）——静默跳过，其余求值不受牵连。
    activation = {
        key: json_to_cel(value) for key, value in payload.items() if _CEL_IDENT.match(key)
    }
    env = celpy.Environment()
    program = env.program(tree, functions=cast(Any, dict(functions)) if functions else None)
    # 求值窗口内抬高递归预算：护栏承诺「链项 ≤256 放行」，而 celpy 在每次
    # Environment() 构造时把进程递归上限**无条件**压回 2500——3.12 的帧深
    # 在 2500 下余量尚可，3.11 更深、边界公式即触 RecursionError 被归一成
    # FormulaError（护栏契约变成解释器彩票）。此处取 max(当前, 上限) 并在
    # finally 恢复；3.11+ 纯 Python 帧递归走堆栈、6000 无段栈风险。
    original_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(original_limit, 6000))
    try:
        return program.evaluate(activation)
    except FormulaError:
        raise
    except Exception as exc:  # CELEvalError / RecursionError 兜底归一
        # 消息截断：celpy 的 CELEvalError 字符串自带整个求值环境
        # 转储（NameContainer repr，数 KB）——有效信息在开头（缺失键名），全文
        # 透出经 web/REST 错误体既是变量值泄漏面、也让 toast 不可读
        detail = str(exc)
        if len(detail) > 300:
            detail = detail[:300] + "…（求值环境转储已截断）"
        raise FormulaError(f"公式求值错误：{formula!r}（{detail}）") from exc
    finally:
        sys.setrecursionlimit(original_limit)


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
    return bool(_evaluate(formula, variables, functions))


def evaluate_formula_value(
    formula: str,
    variables: Mapping[str, Any],
    functions: Mapping[str, Callable[..., object]] | None = None,
) -> Any:
    """在 CEL 求值**值表达式**，原样返回结果（不做条件位的 bool 收敛）。

    值位点（取人/传阅/超时改派/子流程映射/试算）用；条件位继续用
    :func:`evaluate_formula`。护栏与异常归一两者共享；celtypes.BoolType 是
    int 子类（JSON 序列化会变 1），此处收敛为原生 bool，其余类型原样。
    """
    from celpy.celtypes import BoolType

    value = _evaluate(formula, variables, functions)
    return bool(value) if isinstance(value, BoolType) else value


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


def references_identifier(formula: str, name: str) -> bool:
    """公式是否引用标识符 ``name``（静态；编译错返回 False，交由求值路径报错）。

    判断面查无预检用：如 prev_task 未注入而被引用，
    条件位按不满足跳过、取人位贡献空——温和处理不产生技术故障。
    """
    try:
        identifiers, _ = collect_references(formula)
    except FormulaError:
        return False
    return name in identifiers


def root_call(formula: str) -> tuple[str, int] | None:
    """公式**根节点**是全局函数调用时返回 (函数名, 参数个数)，否则 None。

    取人/改派位静态类型检查用：根调用可直接对照 FunctionSignature.returns
    词汇（one_user/users）；非根调用（如 ``'a' + fn(x)``）不做该检查，交给运行时。
    """
    node: Any = _parse(formula)
    while True:
        data = getattr(node, "data", None)
        if data is None:
            return None  # 走到 Token（字面量根）
        if data == "ident_arg":
            args = next((c for c in node.children if getattr(c, "data", None) == "exprlist"), None)
            return str(node.children[0]), 0 if args is None else len(args.children)
        kids = [c for c in node.children if getattr(c, "data", None) is not None]
        if len(kids) != 1:
            return None  # 根是运算/复合表达式，不是单一全局调用
        node = kids[0]
