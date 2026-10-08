"""图级 lint（能力补足 P0-3）：无出口子图与 all 汇聚死臂——全 WARNING。

行业空白点（bpmnlint 至今只有结构规则，执行级 soundness 2026 才立 epic）：
本组把运行时 join gate 的可达性遍历镜像到设计时（共用
:mod:`flowork.definitions.graph`，判据同源）。两条规则均为 WARNING——
不影响发布拦截面，且与运行时语义严格对齐：**不复活**已移除的
``advance.all_conditional_arm`` 判据（v2 §2.3 可达性放行下，条件未命中臂
会落定放行，不构成死锁）。

- ``graph.subgraph.no_exit``：从 start 可达、但沿连线到不了任何 end 的节点
  （封闭子图成环整体无出口——零出边的单点死路已由 ``structure.node.deadend``
  覆盖，本规则专抓子图级封闭）；
- ``join.arm.unreachable``：advance_policy=all 的汇聚节点，某入边的 source
  不在 start 正向可达集——该臂永远等不来令牌（图线画错信号；运行时放行
  不受影响，属图配置质量告警）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.definitions.graph import can_reach_end, forward_reachable

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition
    from flowork.definitions.validator_conditions import IssueFn

__all__ = ["check_graph_lints"]


def check_graph_lints(definition: ProcessDefinition, warning: IssueFn) -> None:
    """图级可达性 lint（无 start/end 时跳过——结构规则已单独拦截）。"""
    reachable = forward_reachable(definition)
    to_end = can_reach_end(definition)
    if reachable is None or to_end is None:
        return

    for node in definition.nodes:
        if node.code in reachable and node.code not in to_end:
            warning(
                "graph.subgraph.no_exit",
                "节点从 start 可达但无法到达任何结束节点（封闭子图，令牌进入即滞留）",
                node=node.code,
            )

    for node in definition.nodes:
        if _is_all_join(definition, node.code):
            for flow in definition.incoming(node.code):
                if flow.source not in reachable:
                    warning(
                        "join.arm.unreachable",
                        f"汇聚臂的源节点 {flow.source} 从 start 不可达，该臂永远等不来令牌"
                        "（运行时可达性放行不受影响，属图配置质量告警）",
                        node=node.code,
                        flow=flow.flow_code(),
                    )


def _is_all_join(definition: ProcessDefinition, node_code: str) -> bool:
    """运行时同款判定：入边 >1 且 advance_policy=='all'（缺省 any 不进门）。"""
    node = definition.node(node_code)
    if node is None or len(definition.incoming(node_code)) <= 1:
        return False
    return (getattr(node, "advance_policy", None) or "any") == "all"
