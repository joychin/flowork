"""变量引用与函数调用静态检查（validator 编排件的规则组三）。

保留词与位点白名单
表驱动自 expr.catalog，规则名不变。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from flowork.definitions.validator_positions import (
    RULE_PREFIX as _RULE_PREFIX,
)
from flowork.definitions.validator_positions import (
    iter_expression_positions,
    lookup_kind,
    mi_config,
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
# （快照注入位）；initiator/now 全位点。
RESERVED_POSITIONS = {entry.name: entry.positions for entry in identifier_entries()}


def check_variable_references(
    definition: ProcessDefinition,
    error: IssueFn,
    warning: IssueFn,
    *,
    expression_functions: ExpressionFunctions | None = None,
) -> None:
    """变量引用与函数调用（全位点单轨 CEL；空声明不再豁免）。

    - 保留字声明（expr.catalog：initiator/now/task）→ ERROR（求值上下文冲突），
      variables 段与 input 契约 properties 同查（input 字段同名会遮蔽引擎注入）；
    - 公式引用未声明变量 → ERROR（静态检查，K8s CRD 体验）——图纸零声明时
      同样检查：引用非保留标识符即 unknown ERROR（此前的「空声明整体跳过」
      让无 variables 段的图纸把引用错误全部推迟到运行期）；
      保留词按目录位点放行——``task.*`` 仅超时改派/会签出口位（快照注入位），
      其余位点引用即错；initiator/now 全位点；
    - 公式调用未注册函数：有注册表 → ERROR（含参数个数）；无注册表 → WARNING；
    - 取人/改派位根调用 returns 非 one_user/users → ERROR（谓词误用提前拦）。
    """
    declared = {v.name for v in definition.variables}
    # input 契约属性同样是变量声明：运行时
    # 白名单＝声明 ∪ input schema ∪ 既有键——设计期只看 variables 段会把
    # 纯契约驱动图纸误拦发布（口径分裂）；同为声明，保留词检查一并覆盖
    input_schema = definition.input if isinstance(definition.input, dict) else None
    declared |= set((input_schema or {}).get("properties") or {})
    for variable in definition.variables:
        if variable.name in RESERVED_IDENTIFIERS:
            error(
                "variables.reserved_name",
                f"{variable.name} 为求值保留标识符（"
                + "、".join(f"{entry.name}={entry.label}" for entry in identifier_entries())
                + "，由引擎注入），不可声明为流程变量",
                node=variable.name,
            )
    for prop in sorted((input_schema or {}).get("properties") or {}):
        if prop in RESERVED_IDENTIFIERS:
            error(
                "input.reserved_name",
                f"{prop} 为求值保留标识符（"
                + "、".join(f"{entry.name}={entry.label}" for entry in identifier_entries())
                + "，由引擎注入），不可作为 input 契约字段——同名会遮蔽引擎注入值",
                node=prop,
            )
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

    # 取人位与候选槽的根调用 returns 白名单不同（槽位表）：
    # assignee/assignees/recipients/reassign 只吃 one_user/users；候选槽额外接受
    # groups/mixed（「混合集自动拆槽」是规范行为，不是误用）。
    subject_returns = {
        "subjects": frozenset({"one_user", "users"}),
        # reassign/escalate fire 时要求单个非空人（jobs/strategies._execute_handover）——
        # 设计期放行 users 曾必炸
        "reassign": frozenset({"one_user"}),
        # 候选槽：多接受组与混合集——「混合集自动拆槽」是规范行为
        "candidates": frozenset({"one_user", "users", "groups", "mixed"}),
    }
    for kind, where, expression, _node_code in iter_expression_positions(definition):
        rule = _RULE_PREFIX[kind]
        # 词汇位点目录按 subjects 登记：候选槽同义（不归一会在候选位误报词汇不可用）
        vocabulary_kind = lookup_kind(kind)
        try:
            idents, functions = collect_references(expression)
        except FormulaError:
            continue  # 语法问题已由 formula.invalid 报告
        if kind == "mapping_output":
            # 子上下文业务变量父定义不可知：仅豁免未声明变量检查。
            # 保留标识符是引擎注入契约、静态可知——位点检查照常
            # （此前连保留词位点检查一并跳过）
            idents = {name for name in idents if name in RESERVED_IDENTIFIERS}
        unknown: set[str] = set()
        for name in idents:
            if name in declared:
                continue
            if name in RESERVED_IDENTIFIERS:
                if vocabulary_kind in RESERVED_POSITIONS[name]:
                    if name == "subject" and kind == "mapping_input":
                        # subject 只在多实例子流程注入（每子一 subject）；
                        # 非 MI 调用映射裸 subject 静默成 None、复合式直接故障
                        # （目录按位放行掩盖了
                        # 非 MI 形态的运行期坑）
                        node = definition.node(_node_code)
                        if node is not None and mi_config(node) is None:
                            error(
                                f"{rule}.subject_without_mi",
                                "subject 仅多实例（multi_instance）子流程的输入映射可用"
                                "——普通子流程不注入；按子主体展开请改用 multi_instance",
                                flow=where,
                            )
                    continue  # 该位点可用（task 窄位点方言 / prev_task 条件+取人位 / 全位点词）
                error(
                    f"{rule}.{name}_unavailable",
                    f"公式引用 {name}.*：该标识符仅 {'/'.join(sorted(RESERVED_POSITIONS[name]))}"
                    " 位提供",
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
                if vocabulary_kind not in entry.positions:
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
        wanted = subject_returns.get(kind)
        if wanted is not None and signatures is not None:
            root = root_call(expression)
            if root is not None:
                name, _arity = root
                registered = signatures.get(name)
                if registered is not None and registered[1] not in wanted:
                    allowed = "、".join(sorted(wanted))
                    error(
                        f"{rule}.function.returns",
                        f"函数 {name}(...) 返回 {registered[1]}（谓词/标量），"
                        f"该位点须返回 {allowed} 词汇",
                        flow=where,
                    )
