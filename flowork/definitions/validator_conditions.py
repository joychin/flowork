"""条件与节点语义校验规则（validator 编排件的规则组二）。

节点语义 / 条件合法性 / 变量引用。CEL 静态检查（kernel-v2 §4.3）落点在本模块。
规则函数统一接 ``(definition, error, warning)``。
"""

from __future__ import annotations

import re
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

__all__ = ["check_condition_legality", "check_node_semantics", "check_variable_references"]

IssueFn = Callable[..., None]

# 公式白名单符号 + Python 关键字：变量引用检查时不算「未知变量」
# （CEL 静态检查换绑后由 expr.cel 薄层取代，见 kernel-v2 §4.3）
_FORMULA_NON_VARS = frozenset(
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
        "and",
        "or",
        "not",
        "in",
        "is",
        "if",
        "else",
    }
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
    """条件合法性：groups/formula 互斥、长度上限、空条件、分支臂缺条件。"""
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
    warning: IssueFn,
) -> None:
    """变量引用（声明了流程变量时启用：条件/公式引用未知变量 → 预警）。"""
    declared = {v.name for v in definition.variables}
    if not declared:
        return
    for flow in definition.flows:
        condition = flow.condition
        if condition is None:
            continue
        refs: set[str] = set()
        for group in condition.groups:
            for item in group:
                root = item.left.split(".", 1)[0]
                if root and root not in declared:
                    refs.add(root)
        if condition.formula:
            for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", condition.formula):
                if name not in declared and name not in _FORMULA_NON_VARS:
                    refs.add(name)
        if refs:
            warning(
                "condition.variable.unknown",
                "条件引用了未声明的流程变量："
                f"{', '.join(sorted(refs))}（可在「流程设置」中声明）",
                flow=flow.flow_code(),
            )
