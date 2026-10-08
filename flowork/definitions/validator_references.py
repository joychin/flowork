"""变量引用与函数调用静态检查（validator 编排件的规则组三）。

从 validator_conditions.py 拆出（单文件 ≤300 行红线）；保留词与位点白名单
表驱动自 expr.catalog（F0），规则名不变。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from flowork.definitions.validator_conditions import (
    _RULE_PREFIX,
    iter_expression_positions,
)
from flowork.expr.catalog import (
    engine_functions,
    identifier_entries,
    reserved_identifiers,
)

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition
    from flowork.spi.ports import ExpressionFunctions

__all__ = ["check_variable_references"]

IssueFn = Callable[..., None]

# 求值上下文注入的保留标识符（expr.catalog 表驱动）：可引用，不可声明为流程变量。
RESERVED_IDENTIFIERS = reserved_identifiers()

# 各保留词的可用位点（expr.catalog 表驱动）：task.* 仅 reassign/exit 位提供
# （spec C.4 快照注入位）；initiator/now 全位点。
RESERVED_POSITIONS = {entry.name: entry.positions for entry in identifier_entries()}


def check_variable_references(
    definition: ProcessDefinition,
    error: IssueFn,
    warning: IssueFn,
    *,
    expression_functions: ExpressionFunctions | None = None,
) -> None:
    """变量引用与函数调用（声明了流程变量时启用，全位点单轨 CEL）。

    - 保留字声明（expr.catalog：initiator/now/task）→ ERROR（求值上下文冲突）；
    - 公式引用未声明变量 → ERROR（静态检查，K8s CRD 体验）；
      保留词按目录位点放行——``task.*`` 仅超时改派/会签出口位（快照注入位，
      spec C.4），其余位点引用即错；initiator/now 全位点；
    - 公式调用未注册函数：有注册表 → ERROR（含参数个数）；无注册表 → WARNING；
    - 取人/改派位根调用 returns 非 one_user/users → ERROR（谓词误用提前拦）。
    """
    declared = {v.name for v in definition.variables}
    for variable in definition.variables:
        if variable.name in RESERVED_IDENTIFIERS:
            error(
                "variables.reserved_name",
                f"{variable.name} 为求值保留标识符（"
                + "、".join(f"{entry.name}={entry.label}" for entry in identifier_entries())
                + "，由引擎注入），不可声明为流程变量",
                node=variable.name,
            )
    if not declared:
        return  # 未启用变量声明（spec §3.2「声明」不约束）：不做引用检查
    signatures: dict[str, tuple[int, str]] | None = None
    if expression_functions is not None:
        signatures = {
            name: (len(registered.signature.params), registered.signature.returns)
            for name, registered in expression_functions.functions().items()
        }

    from flowork.expr.cel import (
        CEL_BUILTIN_FUNCTIONS,
        FormulaError,
        collect_references,
        root_call,
    )

    subject_kinds = frozenset({"subjects", "reassign"})
    for kind, where, expression, _node_code in iter_expression_positions(definition):
        rule = _RULE_PREFIX[kind]
        try:
            idents, functions = collect_references(expression)
        except FormulaError:
            continue  # 语法问题已由 formula.invalid 报告
        if kind == "mapping_output":
            idents = set()  # 子上下文变量父定义不可知：跳过未声明变量检查
        unknown: set[str] = set()
        for name in idents:
            if name in declared:
                continue
            if name in RESERVED_IDENTIFIERS:
                if kind in RESERVED_POSITIONS[name]:
                    continue  # 该位点可用（task 窄位点方言 / prev_task 条件+取人位 / 全位点词）
                error(
                    f"{rule}.{name}_unavailable",
                    f"公式引用 {name}.*：该标识符仅 {'/'.join(sorted(RESERVED_POSITIONS[name]))}"
                    " 位提供（expression-dataface §2）",
                    flow=where,
                )
                continue
            unknown.add(name)
        if unknown:
            error(
                f"{rule}.variable.unknown",
                "公式引用了未声明的流程变量："
                f"{', '.join(sorted(unknown))}"
                f"（{', '.join(sorted(RESERVED_IDENTIFIERS))} 为内置标识符）",
                flow=where,
            )
        engine_fns = engine_functions()
        for name, arities in functions.items():
            if name in CEL_BUILTIN_FUNCTIONS:
                continue
            if name in engine_fns:  # 引擎注入函数（node 等）：目录对照位点与参数个数
                entry = engine_fns[name]
                if kind not in entry.positions:
                    error(
                        f"{rule}.function.position",
                        f"函数 {name}(...) 仅 {'/'.join(sorted(entry.positions))} 位点可用",
                        flow=where,
                    )
                elif any(arity != len(entry.params) for arity in arities):
                    error(
                        f"{rule}.function.arity",
                        f"函数 {name} 期望 {len(entry.params)} 个参数，"
                        f"调用处为 {'/'.join(str(a) for a in sorted(arities))} 个",
                        flow=where,
                    )
                continue
            if signatures is None:
                warning(
                    f"{rule}.function.unknown",
                    f"公式调用函数 {name}(...)：未配置表达式函数注册表"
                    "（EngineConfig.expression_functions），无法静态核对",
                    flow=where,
                )
            elif name not in signatures:
                error(
                    f"{rule}.function.unknown",
                    f"公式调用未注册的函数：{name}(...)（已注册：{', '.join(sorted(signatures))}）",
                    flow=where,
                )
            elif any(arity != signatures[name][0] for arity in arities):
                error(
                    f"{rule}.function.arity",
                    f"函数 {name} 期望 {signatures[name][0]} 个参数，"
                    f"调用处为 {'/'.join(str(a) for a in sorted(arities))} 个",
                    flow=where,
                )
        if kind in subject_kinds and signatures is not None:
            root = root_call(expression)
            if root is not None:
                name, _arity = root
                registered = signatures.get(name)
                if registered is not None and registered[1] not in ("one_user", "users"):
                    error(
                        f"{rule}.function.returns",
                        f"函数 {name}(...) 返回 {registered[1]}（谓词/标量），"
                        "取人位须返回 one_user/users 词汇",
                        flow=where,
                    )
