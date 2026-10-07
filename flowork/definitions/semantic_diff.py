"""语义 diff：两份流程定义的变更清单（设计器发布预览用）。

对齐规则：节点按 code、连线按 source->target、变量按 name；
layout（画布坐标）与 version/status（生命周期字段）不参与比较。
输出为可直接展示的中文变更条目列表，不做任何判定——发布拦截仍由校验器负责。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition

__all__ = ["semantic_diff"]


def _flow_key(source: str, target: str) -> str:
    return f"{source}->{target}"


def semantic_diff(base: ProcessDefinition, target: ProcessDefinition) -> list[dict[str, Any]]:
    """比较 base（已发布）与 target（待发布），返回变更条目列表。"""
    changes: list[dict[str, Any]] = []

    if (base.name or "") != (target.name or ""):
        detail = f"流程名称：「{base.name or base.code}」→「{target.name or target.code}」"
        changes.append({"type": "meta", "action": "changed", "key": "name", "detail": detail})

    # —— 节点 ——
    base_nodes = {n.code: n for n in base.nodes}
    target_nodes = {n.code: n for n in target.nodes}
    for code in sorted(target_nodes.keys() - base_nodes.keys()):
        node = target_nodes[code]
        name = f"「{node.name}」" if getattr(node, "name", "") else ""
        detail = f"新增节点 {name}{code}（{node.kind}）"
        changes.append({"type": "node", "action": "added", "key": code, "detail": detail})
    for code in sorted(base_nodes.keys() - target_nodes.keys()):
        node = base_nodes[code]
        detail = f"删除节点 {code}（{node.kind}）"
        changes.append({"type": "node", "action": "removed", "key": code, "detail": detail})
    for code in sorted(base_nodes.keys() & target_nodes.keys()):
        base_node, target_node = base_nodes[code], target_nodes[code]
        if base_node.kind != target_node.kind:
            detail = f"节点 {code} 类型变更：{base_node.kind} → {target_node.kind}"
            changes.append({"type": "node", "action": "changed", "key": code, "detail": detail})
            continue
        if (base_node.name or "") != (target_node.name or ""):
            old_name = base_node.name or code
            new_name = target_node.name or code
            detail = f"节点 {code} 名称：「{old_name}」→「{new_name}」"
            changes.append({"type": "node", "action": "changed", "key": code, "detail": detail})
        base_dump = base_node.model_dump()
        target_dump = target_node.model_dump()
        base_dump.pop("name", None)
        target_dump.pop("name", None)
        # v2.2 等价归一：旧 timeout 与 deadlines 列表互为退化形态，比较生效列表
        if base_dump.get("kind") == "user_task":
            from flowork.definitions.node import effective_deadlines

            base_dump["__deadlines"] = [d.model_dump() for d in effective_deadlines(base_node)]
            target_dump["__deadlines"] = [d.model_dump() for d in effective_deadlines(target_node)]
            base_dump.pop("timeout", None)
            base_dump.pop("deadlines", None)
            target_dump.pop("timeout", None)
            target_dump.pop("deadlines", None)
        diff_keys = set(base_dump) | set(target_dump)
        fields = sorted(
            k
            for k in diff_keys
            if base_dump.get(k) != target_dump.get(k) and not k.startswith("__")
        )
        if base_dump.get("__deadlines") != target_dump.get("__deadlines"):
            changes.append(
                {
                    "type": "node",
                    "action": "changed",
                    "key": code,
                    "detail": "超时动作配置变更（deadlines 生效列表不同）",
                }
            )
        if fields:
            detail = f"修改节点 {code} 配置：{', '.join(fields)}"
            changes.append({"type": "node", "action": "changed", "key": code, "detail": detail})

    # —— 连线 ——
    base_flows = {_flow_key(f.source, f.target): f for f in base.flows}
    target_flows = {_flow_key(f.source, f.target): f for f in target.flows}
    for key in sorted(target_flows.keys() - base_flows.keys()):
        flow = target_flows[key]
        label = f"（{flow.name}）" if flow.name else ""
        detail = f"新增连线 {key}{label}"
        changes.append({"type": "flow", "action": "added", "key": key, "detail": detail})
    for key in sorted(base_flows.keys() - target_flows.keys()):
        detail = f"删除连线 {key}"
        changes.append({"type": "flow", "action": "removed", "key": key, "detail": detail})
    for key in sorted(base_flows.keys() & target_flows.keys()):
        base_flow, target_flow = base_flows[key], target_flows[key]
        aspects: list[str] = []
        if (base_flow.name or "") != (target_flow.name or ""):
            aspects.append(f"名称「{base_flow.name or '-'}」→「{target_flow.name or '-'}」")
        if base_flow.is_default != target_flow.is_default:
            aspects.append("默认边标记")
        if base_flow.condition != target_flow.condition:
            aspects.append("条件")
        if base_flow.on_take != target_flow.on_take:
            aspects.append("on_take 钩子")
        if aspects:
            detail = f"修改连线 {key}：{'、'.join(aspects)}"
            changes.append({"type": "flow", "action": "changed", "key": key, "detail": detail})

    # —— 流程变量 ——
    base_vars = {v.name: v for v in base.variables}
    target_vars = {v.name: v for v in target.variables}
    for name in sorted(target_vars.keys() - base_vars.keys()):
        detail = f"新增流程变量 {name}"
        changes.append({"type": "variable", "action": "added", "key": name, "detail": detail})
    for name in sorted(base_vars.keys() - target_vars.keys()):
        detail = f"删除流程变量 {name}"
        changes.append({"type": "variable", "action": "removed", "key": name, "detail": detail})
    for name in sorted(base_vars.keys() & target_vars.keys()):
        base_var, target_var = base_vars[name], target_vars[name]
        base_dump = base_var.model_dump()
        target_dump = target_var.model_dump()
        watched = ("type", "label", "sample")
        fields = sorted(k for k in watched if base_dump.get(k) != target_dump.get(k))
        if fields:
            detail = f"修改流程变量 {name}：{', '.join(fields)}"
            changes.append({"type": "variable", "action": "changed", "key": name, "detail": detail})

    return changes
