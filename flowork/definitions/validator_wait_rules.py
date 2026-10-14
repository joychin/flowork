"""等待节点设计时规则（validator 编排件的规则组六）。

从 validator_rules.py 拆出（单文件 ≤300 行红线；code-review 2026-10 清账批
达 324 行）。入口符号经 validator_rules 原位再导出，编排方 import 路径不变。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from flowork.definitions.builtin import KIND_USER_TASK, KIND_WAIT

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition

__all__ = ["check_wait_nodes"]

IssueFn = Callable[..., None]


def check_wait_nodes(definition: ProcessDefinition, error: IssueFn) -> None:
    """等待节点结构规则。"""
    from flowork.expr.cel import FormulaError, collect_references

    nodes = {n.code: n for n in definition.nodes}
    wait_sources = {n.code for n in definition.nodes if n.kind == KIND_WAIT}
    for flow in definition.flows:
        if not flow.condition or flow.source in wait_sources:
            continue
        try:  # 语法问题已由 condition.formula.invalid 报告，此处只管位点
            idents, _functions = collect_references(flow.condition)
        except FormulaError:
            continue
        if "wait" in idents:
            error(
                "condition.wait_source",
                "wait.* 仅等待节点出边条件可用",
                flow=flow.flow_code(),
            )
    from flowork.definitions.wait_node import wait_fields

    for node in definition.nodes:
        if node.kind == KIND_WAIT:
            message, timeout = wait_fields(node)
            if message is None and timeout is None:
                error(
                    "wait.mode.required",
                    "等待节点须声明 message 或 timeout 至少其一（双缺=什么都不等）",
                    node=node.code,
                )
            if message is not None and not message.name.strip():
                error("wait.message.name_empty", "等待消息声明缺 name", node=node.code)
            if not definition.outgoing(node.code):
                error("wait.exit.required", "等待节点必须有出边", node=node.code)
            if any(f.target == node.code for f in definition.outgoing(node.code)):
                # 自环（code-review 2026-10 P2-10 便宜解）：等待收尾（finish_wait）
                # 绕过推进死循环守卫——到时/消息命中即无限重入，每轮都落审计与
                # 事件且无故障单。设计期禁止（合法的循环等待请拆多节点并保证
                # 环上有业务退出条件）。
                error(
                    "wait.self_loop",
                    f"等待节点 {node.code} 的出边指回自身：等待收尾不受推进死循环"
                    "守卫覆盖，到时/消息命中即无限重入——请拆分节点并保证环上有"
                    "业务退出条件（计数变量等）",
                    node=node.code,
                )
        if node.kind == KIND_USER_TASK:
            reject_target = getattr(node, "reject_target", None)
            target_node = nodes.get(reject_target) if reject_target else None
            if target_node is not None and target_node.kind == KIND_WAIT:
                error(
                    "wait.reject_target",
                    f"驳回目标不可为等待节点：{reject_target}（驳回复活到「等」上语义不成立）",
                    node=node.code,
                )
