"""结构规则（validator 编排件的规则组二）：唯一性 / kind 注册 / 端点 / 可达性 /
平行重边（拍板 5 方案 A）。

从 validator_rules.py 拆出（单文件 ≤300 行红线）。入口符号经 validator_rules
原位再导出，编排方 import 路径不变。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from flowork.definitions.builtin import KIND_END, KIND_START, KIND_USER_TASK

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition
    from flowork.runtime.registry import NodeRegistry

__all__ = ["check_structure"]

IssueFn = Callable[..., None]


def check_structure(
    definition: ProcessDefinition,
    registry: NodeRegistry,
    error: IssueFn,
    warning: IssueFn,
) -> None:
    """唯一性 / kind 注册 / start-end 结构 / 端点存在 / 出入边完备 / 可达性。"""
    nodes = {n.code: n for n in definition.nodes}

    # —— 唯一性与编码 ——
    seen: set[str] = set()
    for node in definition.nodes:
        if not node.code:
            error("node.code.empty", "节点编码为空")
        if node.code in seen:
            error("node.code.duplicate", f"节点编码重复：{node.code}", node=node.code)
        seen.add(node.code)

    # —— kind 注册（G2：未知 kind 设计期拦截）——
    for node in definition.nodes:
        if node.kind not in registry:
            error(
                "node.kind.unregistered",
                f"未注册的节点类型 kind={node.kind!r}（已注册：{', '.join(registry.kinds())}）",
                node=node.code,
            )

    # —— start/end 结构 ——
    starts = [n for n in definition.nodes if n.kind == KIND_START]
    ends = [n for n in definition.nodes if n.kind == KIND_END]
    if len(starts) != 1:
        error("structure.start.count", f"必须有且仅有一个 start 节点，当前 {len(starts)} 个")
    if not ends:
        error("structure.end.missing", "至少需要一个 end 节点")

    for node in definition.nodes:
        if node.kind == KIND_START and definition.incoming(node.code):
            error("structure.start.incoming", "start 节点不允许有入边", node=node.code)
        if node.kind == KIND_END and definition.outgoing(node.code):
            error("structure.end.outgoing", "end 节点不允许有出边", node=node.code)

    # —— 平行重边（拍板 5 方案 A）：同一对节点多条连线＝改版对比与破坏性
    # —— 判定的半盲形态（diff/R4 按 source→target 建键只见一条）——设计期
    # —— 禁止；多条件到同一目标用多条不同 source 或改条件表达式。
    edge_pairs: dict[tuple[str, str], str] = {}
    for flow in definition.flows:
        pair = (flow.source, flow.target)
        if pair in edge_pairs:
            error(
                "structure.flow.duplicate",
                f"节点 {flow.source} → {flow.target} 存在平行重边（与 "
                f"{edge_pairs[pair]} 重复）：改版对比与破坏性判定对该形态半盲，"
                "请合并为一条（多条件用 && / || 表达）",
                flow=flow.flow_code(),
            )
        else:
            edge_pairs[pair] = flow.flow_code()

    # —— 连线端点存在性 ——
    for flow in definition.flows:
        for end, role in ((flow.source, "source"), (flow.target, "target")):
            if end not in nodes:
                error(
                    "flow.endpoint.missing",
                    f"连线 {role} 节点不存在：{end}",
                    flow=flow.flow_code(),
                )

    # —— 入边/出边完备性 ——
    for node in definition.nodes:
        if node.kind == KIND_START:
            continue
        if not definition.incoming(node.code):
            warning("structure.node.unreachable", "节点不可达（无入边）", node=node.code)
        if node.kind != KIND_END and not definition.outgoing(node.code):
            error("structure.node.deadend", "非 end 节点必须有出边", node=node.code)

    # —— 从 start 出发的可达性 ——
    if starts:
        reachable: set[str] = set()
        queue = [starts[0].code]
        while queue:
            current = queue.pop()
            if current in reachable:
                continue
            reachable.add(current)
            queue.extend(f.target for f in definition.outgoing(current) if f.target in nodes)
        for node in definition.nodes:
            if node.code not in reachable:
                warning("structure.node.unreachable", "节点从 start 不可达", node=node.code)

    # —— 会签出口字段错位（第十九轮走查发现）：approve_exit/disapprove_exit 写在
    # —— 节点顶层会被 extra 袋静默收下，而行为只读 multi_instance 内同名字段——
    # —— 出口公式零告警失效、静默回退全票。设计期拦下并指路（可见优于静默）。
    for node in definition.nodes:
        if node.kind != KIND_USER_TASK:
            continue
        for field in ("approve_exit", "disapprove_exit"):
            if getattr(node, field, None) is not None:
                error(
                    "user_task.exit.misplaced",
                    f"{field} 写在节点顶层不生效（行为只读 multi_instance 内同名字段）——"
                    f"请放进 multi_instance",
                    node=node.code,
                )
