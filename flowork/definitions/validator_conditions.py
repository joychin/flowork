"""条件与节点语义校验规则（validator 编排件的规则组二）。

节点语义 / 条件合法性 / 变量引用与 **CEL 静态检查薄层**（kernel-v2 §4.3）：
公式支经 ``expr.cel`` 编译 + ``collect_references`` 收集，对照已声明变量与
ExpressionFunctions 注册表签名——未声明变量 ERROR（K8s CRD 体验）、未注册函数
有注册表 ERROR / 无注册表 WARNING（诚实分级，照 org_configured 先例）。
groups 支维持启发式 WARNING（原语义）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from flowork.definitions.builtin import (
    KIND_BRANCH,
    KIND_CC_TASK,
    KIND_USER_TASK,
)
from flowork.definitions.flow import MAX_FORMULA_LENGTH

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition
    from flowork.spi.ports import ExpressionFunctions

__all__ = ["check_condition_legality", "check_node_semantics", "check_variable_references"]

IssueFn = Callable[..., None]

# 求值上下文注入的保留标识符：可引用，不可声明为流程变量。
RESERVED_IDENTIFIERS = frozenset({"initiator"})


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
    """条件合法性：groups/formula 互斥、长度上限、空条件、CEL 编译、分支臂缺条件。"""
    from flowork.expr.cel import FormulaError, collect_references

    nodes = {n.code: n for n in definition.nodes}
    for flow in definition.flows:
        condition = flow.condition
        if condition is None:
            continue
        if condition.groups and condition.formula:
            error(
                "condition.both_set",
                "条件组与公式只允许其一（条件组优先，公式易被忽略）",
                flow=flow.flow_code(),
            )
        if condition.formula and len(condition.formula) > MAX_FORMULA_LENGTH:
            error(
                "condition.formula.too_long",
                f"公式长度 {len(condition.formula)} 超过上限 {MAX_FORMULA_LENGTH}",
                flow=flow.flow_code(),
            )
        if not condition.groups and not condition.formula:
            error(
                "condition.empty",
                "条件为空（groups 与 formula 均未设置）",
                flow=flow.flow_code(),
            )
        if condition.formula:
            try:  # 编译 + 护栏 + 引用收集一次完成（语法错在发布前拦截）
                collect_references(condition.formula)
            except FormulaError as exc:
                error(
                    "condition.formula.invalid",
                    f"公式不可用：{exc}",
                    flow=flow.flow_code(),
                )
        if not flow.is_default and not condition and definition.outgoing(flow.source):
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
    """变量引用与函数调用（声明了流程变量时启用）。

    - 保留字声明（initiator）→ ERROR（求值上下文冲突）；
    - groups 引用未声明变量 → WARNING（启发式，原语义）；
    - 公式（CEL）引用未声明变量 → ERROR（静态检查，K8s CRD 体验）；
    - 公式调用未注册函数：有注册表 → ERROR（含参数个数）；无注册表 → WARNING（无法核对）。
    """
    declared = {v.name for v in definition.variables}
    for variable in definition.variables:
        if variable.name in RESERVED_IDENTIFIERS:
            error(
                "variables.reserved_name",
                f"{variable.name} 为求值保留标识符（发起人，由引擎注入），不可声明为流程变量",
                node=variable.name,
            )
    if not declared:
        return  # 未启用变量声明（spec §3.2「声明」不约束）：不做引用检查
    signatures: dict[str, int] | None = None
    if expression_functions is not None:
        signatures = {
            name: len(registered.signature.params)
            for name, registered in expression_functions.functions().items()
        }

    from flowork.expr.cel import CEL_BUILTIN_FUNCTIONS, FormulaError, collect_references

    for flow in definition.flows:
        condition = flow.condition
        if condition is None:
            continue
        refs: set[str] = set()
        for group in condition.groups:
            for item in group:
                root = item.left.split(".", 1)[0]
                if root and root not in declared and root not in RESERVED_IDENTIFIERS:
                    refs.add(root)
        if condition.formula:
            try:
                idents, functions = collect_references(condition.formula)
            except FormulaError:
                idents, functions = set(), {}  # 语法问题已由 condition.formula.invalid 报告
            unknown = {
                name for name in idents if name not in declared and name not in RESERVED_IDENTIFIERS
            }
            if unknown:
                error(
                    "condition.variable.unknown",
                    "公式引用了未声明的流程变量："
                    f"{', '.join(sorted(unknown))}"
                    f"（{', '.join(sorted(RESERVED_IDENTIFIERS))} 为内置标识符）",
                    flow=flow.flow_code(),
                )
            for name, arities in functions.items():
                if name in CEL_BUILTIN_FUNCTIONS:
                    continue
                if signatures is None:
                    warning(
                        "condition.function.unknown",
                        f"公式调用函数 {name}(...)：未配置表达式函数注册表"
                        "（EngineConfig.expression_functions），无法静态核对",
                        flow=flow.flow_code(),
                    )
                elif name not in signatures:
                    error(
                        "condition.function.unknown",
                        f"公式调用未注册的函数：{name}(...)（已注册："
                        f"{', '.join(sorted(signatures))}）",
                        flow=flow.flow_code(),
                    )
                elif any(arity != signatures[name] for arity in arities):
                    error(
                        "condition.function.arity",
                        f"函数 {name} 期望 {signatures[name]} 个参数，"
                        f"调用处为 {'/'.join(str(a) for a in sorted(arities))} 个",
                        flow=flow.flow_code(),
                    )
        if refs:
            warning(
                "condition.variable.unknown",
                "条件组引用了未声明的流程变量："
                f"{', '.join(sorted(refs))}（可在「流程设置」中声明）",
                flow=flow.flow_code(),
            )
