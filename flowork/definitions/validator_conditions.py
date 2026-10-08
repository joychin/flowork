"""条件与节点语义校验规则（validator 编排件的规则组二）。

节点语义 / 表达式合法性 / 变量引用与 **CEL 静态检查薄层**（kernel-v2 §4.3）。
位点统一（§4.2）：静态检查覆盖全部表达式位——连线条件、取人（assignee/
assignees/recipients）、超时改派（reassign_to）、子流程映射值；各公式经
``expr.cel`` 编译 + ``collect_references`` 收集，对照已声明变量与
ExpressionFunctions 注册表签名。未声明变量 ERROR（K8s CRD 体验）、未注册函数
有注册表 ERROR / 无注册表 WARNING（诚实分级，照 org_configured 先例）；
取人/改派位根调用另做 returns 类型对位（谓词 bool 误用即拦）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from flowork.definitions.builtin import (
    KIND_BRANCH,
    KIND_CALL_ACTIVITY,
    KIND_CC_TASK,
    KIND_USER_TASK,
)
from flowork.definitions.flow import MAX_FORMULA_LENGTH
from flowork.expr.catalog import (
    engine_functions,
    identifier_entries,
    reserved_identifiers,
)

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition
    from flowork.spi.ports import ExpressionFunctions

__all__ = [
    "check_condition_legality",
    "check_node_semantics",
    "check_variable_references",
    "iter_expression_positions",
]

IssueFn = Callable[..., None]

# 求值上下文注入的保留标识符（expr.catalog 表驱动）：可引用，不可声明为流程变量。
RESERVED_IDENTIFIERS = reserved_identifiers()

# 各保留词的可用位点（expr.catalog 表驱动）：task.* 仅 reassign/exit 位提供
# （spec C.4 快照注入位）；initiator/now 全位点。
RESERVED_POSITIONS = {entry.name: entry.positions for entry in identifier_entries()}

# 位点 kind → 规则名前缀（连线位沿用既有 condition.* 规则名，节点位用 expression.*）
_RULE_PREFIX = {
    "condition": "condition",
    "subjects": "expression",
    "reassign": "expression",
    "exit": "expression",
    "mapping_input": "expression",
    "mapping_output": "expression",
}


def iter_expression_positions(definition: ProcessDefinition) -> Iterator[tuple[str, str, str, str]]:
    """遍历全部表达式位：yield (kind, where, expression, node)。

    kind ∈ condition（连线条件）/ subjects（取人：assignee·assignees·recipients）/
    reassign（超时改派）/ exit（会签出口 approve_exit·disapprove_exit）/
    mapping_input·mapping_output（子流程映射值，后者在子上下文求值——父定义
    静态不可知其变量，跳过未声明变量检查）。task.* 在 reassign 与 exit 位可用。
    """
    for flow in definition.flows:
        if flow.condition:
            yield "condition", flow.flow_code(), flow.condition, flow.source
    for node in definition.nodes:
        if node.kind == KIND_USER_TASK:
            assignee = getattr(node, "assignee", None)
            if assignee:
                yield "subjects", f"{node.code}.assignee", assignee, node.code
            for index, expression in enumerate(getattr(node, "assignees", None) or []):
                yield "subjects", f"{node.code}.assignees[{index}]", expression, node.code
            mi = getattr(node, "multi_instance", None)
            if mi is not None:
                if mi.approve_exit:
                    yield (
                        "exit",
                        f"{node.code}.multi_instance.approve_exit",
                        mi.approve_exit,
                        node.code,
                    )
                if mi.disapprove_exit:
                    yield (
                        "exit",
                        f"{node.code}.multi_instance.disapprove_exit",
                        mi.disapprove_exit,
                        node.code,
                    )
        if node.kind == KIND_CC_TASK:
            for index, expression in enumerate(getattr(node, "recipients", None) or []):
                yield "subjects", f"{node.code}.recipients[{index}]", expression, node.code
        for index, deadline in enumerate(getattr(node, "deadlines", None) or []):
            if deadline.reassign_to:
                yield (
                    "reassign",
                    f"{node.code}.deadlines[{index}].reassign_to",
                    deadline.reassign_to,
                    node.code,
                )
        if node.kind == KIND_CALL_ACTIVITY:
            for target, expression in (getattr(node, "input_mappings", None) or {}).items():
                yield "mapping_input", f"{node.code}.input_mappings.{target}", expression, node.code
            for target, expression in (getattr(node, "output_mappings", None) or {}).items():
                yield (
                    "mapping_output",
                    f"{node.code}.output_mappings.{target}",
                    expression,
                    node.code,
                )


def check_node_semantics(
    definition: ProcessDefinition,
    error: IssueFn,
    warning: IssueFn,
) -> None:
    """审批/传阅节点的参与者与驳回目标。"""
    nodes = {n.code: n for n in definition.nodes}
    for node in definition.nodes:
        if node.kind == KIND_USER_TASK:
            has_participant = any(
                (
                    getattr(node, "assignee", None),
                    getattr(node, "assignees", None),
                    getattr(node, "candidate_users", None),
                    getattr(node, "candidate_groups", None),
                )
            )
            if not has_participant:
                warning("user_task.no_participant", "审批节点未配置任何参与者", node=node.code)
            reject_target = getattr(node, "reject_target", None)
            if reject_target and reject_target not in nodes:
                error(
                    "user_task.reject_target.missing",
                    f"驳回目标节点不存在：{reject_target}",
                    node=node.code,
                )
        if node.kind == KIND_CC_TASK and not getattr(node, "recipients", None):
            warning("cc_task.no_recipients", "传阅节点未配置接收人", node=node.code)


def check_condition_legality(
    definition: ProcessDefinition,
    error: IssueFn,
    warning: IssueFn,
) -> None:
    """表达式合法性（全位点）：空公式、长度上限、编译与护栏、分支臂缺条件。"""
    from flowork.expr.cel import FormulaError, collect_references

    nodes = {n.code: n for n in definition.nodes}
    for kind, where, expression, _node_code in iter_expression_positions(definition):
        rule = _RULE_PREFIX[kind]
        if not expression.strip():
            error(f"{rule}.empty", "条件公式为空字符串（无条件请置 null）", flow=where)
        elif len(expression) > MAX_FORMULA_LENGTH:
            error(
                f"{rule}.formula.too_long",
                f"公式长度 {len(expression)} 超过上限 {MAX_FORMULA_LENGTH}",
                flow=where,
            )
        else:
            try:  # 编译 + 护栏 + 引用收集一次完成（语法错在发布前拦截）
                collect_references(expression)
            except FormulaError as exc:
                error(f"{rule}.formula.invalid", f"公式不可用：{exc}", flow=where)
    for flow in definition.flows:
        if not flow.is_default and not flow.condition and definition.outgoing(flow.source):
            source_node = nodes.get(flow.source)
            source_is_branch = source_node is not None and source_node.kind == KIND_BRANCH
            if source_is_branch:
                warning(
                    "condition.missing_on_branch",
                    "分支节点出边未挂条件（仅默认边允许无条件）",
                    flow=flow.flow_code(),
                )


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
