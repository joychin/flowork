"""表达式位点遍历（validator 编排件的位点知识）。

全仓「哪些字段是 CEL 公式」的**唯一清单**：`iter_expression_positions` 供
validator_conditions（合法性）与 validator_references（引用/函数对位）共用；
规则名前缀表 `_RULE_PREFIX` 同处（位点 kind → 规则名，连线位沿用 condition.*、
节点位用 expression.*、等待关联键用 wait.correlation.*）。

入口符号原位再导出，
既有 import 路径不变。

**候选槽**：`candidate_users` / `candidate_groups` 与取人位
同列（槽位表），kind=`candidates`——与 `subjects` 只差**接受的返回类型**
（多接受 groups/mixed，见 validator_references 的 returns 对位）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from flowork.definitions.builtin import (
    KIND_CALL_ACTIVITY,
    KIND_CC_TASK,
    KIND_USER_TASK,
    KIND_WAIT,
)
from flowork.definitions.validator_deadlines import deadline_positions

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition

__all__ = ["RULE_PREFIX", "iter_expression_positions", "lookup_kind", "mi_config"]


def mi_config(node: Any) -> Any:
    """multi_instance 归一（builder extra 袋 dict → CallInstanceConfig；仅 call_activity）。"""
    from flowork.definitions.call_instance import CallInstanceConfig

    mi = getattr(node, "multi_instance", None) if node.kind == KIND_CALL_ACTIVITY else None
    return None if mi is None else CallInstanceConfig(**mi) if isinstance(mi, dict) else mi


# 位点 kind → 规则名前缀；candidates 与 subjects 同前缀——规则名不增
# （候选槽的差别只在接受的返回类型，见 validator_references 的 returns 对位）。
RULE_PREFIX = {
    "condition": "condition",
    "subjects": "expression",
    "candidates": "expression",
    "reassign": "expression",
    "exit": "expression",
    "mapping_input": "expression",
    "mapping_output": "expression",
    "wait_correlation": "wait.correlation",
    "completion": "call_mi.completion",  # MI 完成公式（三计数注入，catalog@completion）
}

_RULE_PREFIX = RULE_PREFIX  # 历史名（validator_conditions 内部按旧名引用）


def lookup_kind(kind: str) -> str:
    """词汇位点目录查询用的归一 kind：candidates → subjects。

    候选槽与取人位在 expr.catalog 的词汇位点上同义（prev_task/node() 都可用）；
    不归一就会在候选位被误报「该标识符仅 … 位提供」。
    """
    return "subjects" if kind == "candidates" else kind


def iter_expression_positions(definition: ProcessDefinition) -> Iterator[tuple[str, str, str, str]]:
    """遍历全部表达式位：yield (kind, where, expression, node)。

    kind ∈ condition（连线条件）/ subjects（取人：assignee·assignees·recipients）/
    candidates（候选槽：candidate_users·candidate_groups——每项是 CEL 公式而非字面量清单）/
    reassign（超时改派）/ exit（会签出口 approve_exit·disapprove_exit）/
    mapping_input·mapping_output（子流程映射值，后者在子上下文求值——父定义
    静态不可知其变量，跳过未声明变量检查）。task.* 在 reassign 与 exit 位可用。
    """
    for flow in definition.flows:
        # is not None 而非真值：空串条件要进位让 condition.empty 规则拦下
        # （2026-10-21 内核 review P1-1——修前 "" 既不是无条件也不是可求值条件，
        # 校验零错误、首次路由即挂起实例）
        if flow.condition is not None:
            yield "condition", flow.flow_code(), flow.condition, flow.source
    for node in definition.nodes:
        if node.kind == KIND_USER_TASK:
            assignee = getattr(node, "assignee", None)
            if assignee:
                yield "subjects", f"{node.code}.assignee", assignee, node.code
            for index, expression in enumerate(getattr(node, "assignees", None) or []):
                yield "subjects", f"{node.code}.assignees[{index}]", expression, node.code
            for index, expression in enumerate(getattr(node, "candidate_users", None) or []):
                yield "candidates", f"{node.code}.candidate_users[{index}]", expression, node.code
            for index, expression in enumerate(getattr(node, "candidate_groups", None) or []):
                yield (
                    "candidates",
                    f"{node.code}.candidate_groups[{index}]",
                    expression,
                    node.code,
                )
            mi = getattr(node, "multi_instance", None)
            if isinstance(mi, dict):  # builder 无 registry 的 dict 袋
                from flowork.definitions.node import MultiInstance

                mi = MultiInstance(**mi)
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
        for where, expression in deadline_positions(node):
            yield "reassign", where, expression, node.code
        if node.kind == KIND_CALL_ACTIVITY:
            mi = mi_config(node)
            if mi is not None:
                # MI subjects＝取人位（每人一子实例，经 resolve_subjects 求值
                # ——缺席曾令语法错/未声明变量/组引用
                # 字面量零告警直通运行期）
                for index, expression in enumerate(mi.subjects or []):
                    yield (
                        "subjects",
                        f"{node.code}.multi_instance.subjects[{index}]",
                        expression,
                        node.code,
                    )
            if mi is not None and mi.completion != "all":  # 完成公式位
                where = f"{node.code}.multi_instance.completion"
                yield "completion", where, mi.completion, node.code
            for target, expression in (getattr(node, "input_mappings", None) or {}).items():
                yield "mapping_input", f"{node.code}.input_mappings.{target}", expression, node.code
            for target, expression in (getattr(node, "output_mappings", None) or {}).items():
                yield (
                    "mapping_output",
                    f"{node.code}.output_mappings.{target}",
                    expression,
                    node.code,
                )
        if node.kind == KIND_WAIT:
            from flowork.definitions.wait_node import wait_fields

            message, _timeout = wait_fields(node)
            if message is not None and message.correlation_key:
                yield (
                    "wait_correlation",
                    f"{node.code}.message.correlation_key",
                    message.correlation_key,
                    node.code,
                )
            for target, expression in (getattr(node, "output_mappings", None) or {}).items():
                yield (
                    "mapping_output",
                    f"{node.code}.output_mappings.{target}",
                    expression,
                    node.code,
                )
