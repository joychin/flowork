"""条件与节点语义校验规则（validator 编排件的规则组二）。

节点语义 / 表达式合法性 / **CEL 静态检查薄层**（kernel-v2 §4.3）。
位点统一（§4.2）：静态检查覆盖全部表达式位——连线条件、取人（assignee/
assignees/recipients）、超时改派（reassign_to）、子流程映射值；各公式经
``expr.cel`` 编译 + ``collect_references`` 收集，对照已声明变量与
ExpressionFunctions 注册表签名。未声明变量 ERROR（K8s CRD 体验）、未注册函数
有注册表 ERROR / 无注册表 WARNING（诚实分级，照 org_configured 先例）；
取人/改派位根调用另做 returns 类型对位（谓词 bool 误用即拦）。
变量引用细查拆至 validator_references.py（规则组三）。
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

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition

__all__ = [
    "check_condition_legality",
    "check_node_semantics",
    "iter_expression_positions",
]

IssueFn = Callable[..., None]

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
