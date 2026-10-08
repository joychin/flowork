"""图遍历共享件（能力补足 P0-3）：运行时放行门与设计时 lint 同源。

engine/join_gate 的反向 BFS（上游盆地）下沉至此——definitions 是下层包
（engine→dependencies 方向合法、反向禁止），运行时 all 汇聚放行判定与
设计时图级 lint（validator_graph）共用同一份遍历，杜绝镜像漂移：
**lint 的告警判据必须与运行时放行语义逐字一致**（v2 §2.3）。
全部纯结构判定，不评条件、不读令牌。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition

__all__ = ["can_reach_end", "forward_reachable", "upstream_basin"]


def upstream_basin(definition: ProcessDefinition, node_code: str) -> frozenset[str]:
    """能沿 ≥1 条连线到达 ``node_code`` 的节点集合（含 ``node_code`` 自身，经环）。"""
    seen = {node_code}
    frontier = [node_code]
    while frontier:
        for flow in definition.incoming(frontier.pop()):
            if flow.source not in seen:
                seen.add(flow.source)
                frontier.append(flow.source)
    return frozenset(seen)


def forward_reachable(definition: ProcessDefinition) -> frozenset[str] | None:
    """从 start 出发沿连线可达的节点集合；无 start 节点时返回 None（结构规则已拦）。"""
    start = definition.start_node()
    if start is None:
        return None
    seen = {start.code}
    frontier = [start.code]
    while frontier:
        for flow in definition.outgoing(frontier.pop()):
            if flow.target not in seen:
                seen.add(flow.target)
                frontier.append(flow.target)
    return frozenset(seen)


def can_reach_end(definition: ProcessDefinition) -> frozenset[str] | None:
    """能沿 ≥1 条连线到达任一 end 的节点集合（含 end 自身，经环）。

    等于全部 end 节点的上游盆地并集；无 end 节点时返回 None（结构规则已拦）。
    """
    ends = [node.code for node in definition.nodes if node.kind == "end"]
    if not ends:
        return None
    seen: set[str] = set(ends)
    frontier = list(ends)
    while frontier:
        for flow in definition.incoming(frontier.pop()):
            if flow.source not in seen:
                seen.add(flow.source)
                frontier.append(flow.source)
    return frozenset(seen)
