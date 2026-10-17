"""场景断言与覆盖评估（scenario 运行器的评估面，拆分守行数红线）。

断言口径＝业务效果（终态/到访/已办行/变量/选路/待办），期望值支持
equals/in/op+value/contains/not_contains；覆盖＝节点与边（照明指标，非门禁）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.engine.scenario import ScenarioReport

__all__ = [
    "check_assertion",
    "compute_coverage",
    "definition_of_instance",
    "evaluate_assertions",
    "match_expected",
    "visited_nodes",
]


def evaluate_assertions(
    engine: ProcessEngine,
    instance_id: str,
    assertions: list[dict[str, Any]],
    report: ScenarioReport,
) -> None:
    from flowork.engine.rehearsal import rehearsal_state

    state = rehearsal_state(engine, instance_id)
    for item in assertions:
        kind = str(item.get("type"))
        ok, actual = check_assertion(kind, item, state, report)
        entry: dict[str, Any] = {
            "type": kind,
            "ok": ok,
            "actual": actual,
            "expected": {k: v for k, v in item.items() if k != "type"},
            "message": item.get("message") or "",
        }
        report.assertions.append(entry)


def check_assertion(
    kind: str, item: dict[str, Any], state: dict[str, Any], report: ScenarioReport
) -> tuple[bool, Any]:
    instance = state["instance"]
    tasks = state["task_history"]
    if kind == "final_state":
        actual = instance["state"]
        return match_expected(actual, item), actual
    if kind == "visited":  # 到访节点（路由/已办/选中边推出的确定性集合）
        nodes = visited_nodes(state, report)
        if "contains" in item:
            return set(item["contains"]) <= nodes, sorted(nodes)
        if "not_contains" in item:
            return not (set(item["not_contains"]) & nodes), sorted(nodes)
        return bool(nodes), sorted(nodes)
    if kind == "task":  # 已办行：node × outcome × by × count
        rows = [
            row
            for row in tasks
            if ("node" not in item or row["node"] == item["node"])
            and ("outcome" not in item or row["outcome"] == item["outcome"])
            and ("by" not in item or row["acted_by"] == item["by"])
        ]
        count = item.get("count")
        return (len(rows) == count if count is not None else bool(rows)), len(rows)
    if kind == "variable":
        actual = state["variables"].get(str(item.get("name")))
        return match_expected(actual, item), actual
    if kind == "route":  # 某节点选中的出边包含指定 flow id
        selected: list[str] = []
        for row in state["routes"]:
            if row["node"] == item.get("node"):
                selected.extend(str(edge) for edge in row["selected"])
        expected = item.get("selected")
        return (expected in selected) if expected is not None else bool(selected), selected
    if kind == "pending":  # 停住的待办：empty=true 或无该节点
        pending_nodes = [t["activity_code"] for t in state["tasks"]]
        if item.get("empty"):
            return not pending_nodes, pending_nodes
        return (
            (item.get("node") in pending_nodes) if item.get("node") else bool(pending_nodes)
        ), pending_nodes
    return False, f"未知断言类型：{kind}"


def match_expected(actual: Any, item: dict[str, Any]) -> bool:
    if "equals" in item:
        return bool(actual == item["equals"])
    if "in" in item:
        return actual in item["in"]
    if "op" in item and "value" in item:
        op, value = str(item["op"]), item["value"]
        try:
            return bool(
                {
                    ">": actual > value,
                    ">=": actual >= value,
                    "<": actual < value,
                    "<=": actual <= value,
                    "!=": actual != value,
                }[op]
            )
        except (KeyError, TypeError):
            return False
    return True  # 无期望＝只做存在性（配合 count/contains 用法）


def visited_nodes(state: dict[str, Any], report: Any = None) -> set[str]:
    """到访节点：路由行 source + 选中边两端 + 已办行 node（全确定性）。"""
    nodes = {str(row["node"]) for row in state["routes"] if row["node"]}
    for row in state["routes"]:
        for edge in row["selected"]:
            nodes.update(part for part in str(edge).split("->") if part)
    nodes |= {str(row["node"]) for row in state["task_history"]}
    if report is not None:
        nodes |= {step["node"] for step in report.steps if step.get("kind") == "task"}
    return nodes


def compute_coverage(engine: ProcessEngine, instance_id: str) -> dict[str, Any]:
    """节点/边覆盖（照明指标，非门禁）：场景走过 vs 图里全部。"""
    from flowork.engine.rehearsal import rehearsal_state

    try:
        definition = definition_of_instance(engine, instance_id)
    except FloworkError:
        return {}
    state = rehearsal_state(engine, instance_id)
    nodes_all = {node.code for node in definition.nodes}
    edges_all = {f"{flow.source}->{flow.target}" for flow in definition.flows}
    nodes_hit = visited_nodes(state, None)
    nodes_hit |= {str(row["node"]) for row in state["routes"]}
    edges_hit: set[str] = set()
    for row in state["routes"]:
        edges_hit.update(str(edge) for edge in row["selected"])
    for step in state["task_history"]:
        edges_hit.add(f"{step['node']}->?")  # 已办行无出边信息：仅节点维度可用
    edges_hit = {edge for edge in edges_hit if edge in edges_all}
    return {
        "nodes": {
            "covered": sorted(nodes_hit & nodes_all),
            "missing": sorted(nodes_all - nodes_hit),
            "total": len(nodes_all),
        },
        "edges": {
            "covered": sorted(edges_hit),
            "missing": sorted(edges_all - edges_hit),
            "total": len(edges_all),
        },
    }


def definition_of_instance(engine: ProcessEngine, instance_id: str) -> Any:
    def _read(ctx: Any) -> Any:
        from flowork.persistence.orm import ProcessInstanceRow

        instance = ctx.session.get(ProcessInstanceRow, instance_id)
        assert instance is not None
        return engine.kernel_for(ctx).definition_of(ctx.session, instance)

    return engine.execute(_read)
