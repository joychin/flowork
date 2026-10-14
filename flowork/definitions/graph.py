"""图遍历共享件：运行时放行门与设计时 lint 同源。

engine/join_gate 的反向 BFS（上游盆地）下沉至此——definitions 是下层包
（engine→dependencies 方向合法、反向禁止），运行时 all 汇聚放行判定与
设计时图级 lint（validator_graph）共用同一份遍历，杜绝镜像漂移：
**lint 的告警判据必须与运行时放行语义逐字一致**。
全部纯结构判定，不评条件、不读令牌。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition

__all__ = [
    "can_reach_end",
    "downstream_basin",
    "forward_reachable",
    "racing_basin",
    "upstream_basin",
]


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


def racing_basin(definition: ProcessDefinition, node_code: str) -> frozenset[str]:
    """any 竞速臂判定：能到达 ``node_code`` 的节点集合
    （含自身）——**直接前驱恒计入**；更深的祖先沿图扩展，但**途经其他多入边节点
    即止**（不越过别的汇合域）。

    与 upstream_basin 的差别：不做全域反向 BFS——穿过多入边节点的路径不算，
    那些分支属于**它自己的**汇合域（例：环回世界里 b 的令牌要经 k 才能到 a，
    b 不是 a 的竞速臂而是 k 的等待臂，不能在 a 的竞速收尾里被误杀）；而直接
    前驱是经典竞速兄弟（旧语义即取消），无论它自身是否多入边。纯结构判定，
    不评条件、不读令牌。
    """
    joins = {node.code for node in definition.nodes if len(definition.incoming(node.code)) > 1}
    joins.discard(node_code)
    seen = {node_code}
    frontier = [node_code]
    while frontier:
        for flow in definition.incoming(frontier.pop()):
            if flow.source in seen:
                continue
            seen.add(flow.source)
            if flow.source not in joins:
                frontier.append(flow.source)  # 途经其他汇合点即止（其上游属另一汇合域）
    return frozenset(seen)


def downstream_basin(definition: ProcessDefinition, node_code: str) -> frozenset[str]:
    """从 ``node_code`` 出发沿连线可达的节点集合（含自身，经环）——upstream_basin 的对偶。

    撤回下游闸门用它界定「本节点的下游」；不评条件：
    未命中的条件分支也在集合内（保守方向——多拒不少拒）。
    """
    seen = {node_code}
    frontier = [node_code]
    while frontier:
        for flow in definition.outgoing(frontier.pop()):
            if flow.target not in seen:
                seen.add(flow.target)
                frontier.append(flow.target)
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
