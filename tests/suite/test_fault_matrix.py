"""故障注入×恢复矩阵（测试套件完善批 4/5）：结构敏感位 × resolve 五动作。

世界＝world_flows_fault 四位置（链条中段/汇聚臂/子流程内/MI 子内），变量
boom 控制 handler 炸/不炸。实证落点：子内与 MI 内故障在失败命令内整体回滚
（无孤儿子实例），故障单一律挂父实例（错误归因帧指到子内节点）。
矩阵：{位置} × {fix 后 resume/retry/skip，cancel，fail}——前三列恢复后可
确定性办完（completed），后两列终态 cancelled/terminated。链条位另钉
「根因未修时 resume/retry 复发」（二次故障单）。
断言全家：批次一账本（票数/对账/终态契约/树闭合）+ 悬空 + 故障单全关 +
汇聚臂位路由行择道。
"""

from __future__ import annotations

import pytest

from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from tests.suite.invariants import (
    assert_child_tree_closed,
    assert_full_ledger,
    assert_instance_ledger,
)
from tests.suite.sequence_audit import route_rows
from tests.suite.world_flows_fault import (
    fragile_arm_definition,
    fragile_chain_definition,
    fragile_child_definition,
    fragile_kid_definition,
    fragile_mi_definition,
)

POSITIONS = ["chain", "arm", "child", "mi"]
TERMINAL_BY_ACTION = {
    "resume": "completed",
    "retry": "completed",
    "skip": "completed",
    "cancel": "cancelled",
    "fail": "terminated",
}


@pytest.fixture
def engine() -> ProcessEngine:
    def explode_if(ctx, params):
        if ctx.variables.get("boom"):
            raise RuntimeError("外部接口炸了")
        ctx.variables["passed"] = True

    instance = ProcessEngine()
    instance.register_handler("explode_if", explode_if)
    for definition in (
        fragile_chain_definition(),
        fragile_arm_definition(),
        fragile_kid_definition(),
        fragile_child_definition(),
        fragile_mi_definition(),
    ):
        instance.repository.deploy(definition)
    return instance


def _explode_at_submit(engine: ProcessEngine, position: str) -> str:
    code = f"fragile_{position}"
    iid = engine.runtime.start_process(
        code, start_user="u_dev_wu", variables={"boom": True, "go": True}
    )
    task = engine.task.list_tasks(instance_id=iid, assignee="u_dev_wu")[0]
    with pytest.raises(FloworkError):
        engine.task.complete(task.id, actor="u_dev_wu")
    assert engine.runtime.get_instance(iid).state == "suspended"
    incidents = engine.runtime.list_incidents(instance_id=iid)
    assert len(incidents) == 1 and incidents[0]["state"] == "open"
    # 子内/MI 内故障在失败命令内整体回滚：不得留下任何子实例（孤儿检测前置）
    children = [r for r in engine.history.instances(limit=100_000) if r.parent_id == iid]
    assert children == [], "失败命令回滚应连子实例一起回滚"
    return iid


def _drain_recovered(engine: ProcessEngine, iid: str) -> None:
    """恢复后确定性办完：树上全部待办逐个 complete（人已定死，无候选）。"""
    for _ in range(20):
        tree = {iid}
        frontier = [iid]
        while frontier:
            parent = frontier.pop()
            for row in engine.history.instances(limit=100_000):
                if row.parent_id == parent and row.id not in tree:
                    tree.add(row.id)
                    frontier.append(row.id)
        tasks = [t for tid in tree for t in engine.task.list_tasks(instance_id=tid, state="ready")]
        if not tasks:
            break
        engine.task.complete(tasks[0].id, actor=tasks[0].assignee)
    assert engine.runtime.get_instance(iid).state == "completed"


def _assert_incidents_closed(engine: ProcessEngine, iid: str) -> None:
    for incident in engine.runtime.list_incidents(instance_id=iid):
        assert incident["state"] == "resolved", incident


@pytest.mark.parametrize("position,action", [(p, a) for p in POSITIONS for a in TERMINAL_BY_ACTION])
def test_fault_recovery_matrix(engine: ProcessEngine, position: str, action: str) -> None:
    """{结构位} × {resolve 动作}：终态如期、账本干净、故障单全关。"""
    iid = _explode_at_submit(engine, position)
    incident_id = engine.runtime.list_incidents(instance_id=iid)[0]["id"]
    if action in ("resume", "retry", "skip"):
        engine.runtime.update_variables(iid, {"boom": False}, actor="ops")  # 修复根因
    engine.runtime.resolve_incident(incident_id, action=action, actor="ops")
    if action in ("resume", "retry", "skip"):
        _drain_recovered(engine, iid)
    assert engine.runtime.get_instance(iid).state == TERMINAL_BY_ACTION[action]
    _assert_incidents_closed(engine, iid)
    assert_full_ledger(engine)


def test_arm_position_route_rows_after_skip(engine: ProcessEngine) -> None:
    """汇聚臂位：恢复后路由审计如实记录双臂择道（skip 路径也完整）。"""
    iid = _explode_at_submit(engine, "arm")
    engine.runtime.update_variables(iid, {"boom": False}, actor="ops")
    engine.runtime.resolve_incident(
        engine.runtime.list_incidents(instance_id=iid)[0]["id"], action="skip", actor="ops"
    )
    _drain_recovered(engine, iid)
    gate_rows = [r for r in route_rows(engine, iid) if r["source"] == "gate"]
    assert gate_rows and gate_rows[0]["selected"] == ["gate->boom_arm", "gate->hold"], gate_rows


@pytest.mark.parametrize("action", ["resume", "retry"])
def test_unfixed_failure_recurs(engine: ProcessEngine, action: str) -> None:
    """链条位根因未修：resolve 后再触发仍挂起，二次故障单（复发不静默）。"""
    iid = _explode_at_submit(engine, "chain")
    first = engine.runtime.list_incidents(instance_id=iid)[0]["id"]
    engine.runtime.resolve_incident(first, action=action, actor="ops")
    assert engine.runtime.get_instance(iid).state == "running"
    task = engine.task.list_tasks(instance_id=iid, assignee="u_dev_wu")[0]
    with pytest.raises(FloworkError):
        engine.task.complete(task.id, actor="u_dev_wu")
    assert engine.runtime.get_instance(iid).state == "suspended"
    open_ids = [
        inc["id"]
        for inc in engine.runtime.list_incidents(instance_id=iid)
        if inc["state"] == "open"
    ]
    assert open_ids, "复发必须留新的故障单（不静默吞）"
    # 挂起是有意留下的运行态：跑中途安全账本（票数/对账/终态契约+树闭合），不做悬空收尾
    assert_child_tree_closed(engine)
    assert_instance_ledger(engine, iid)
