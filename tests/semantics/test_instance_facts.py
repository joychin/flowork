"""instance.* 实例全局事实（expression-dataface §2）：id/started_at/business_key。

判断面词汇 v1 收口：三字段全位点注入——条件位路由、取人表达式、会签出口、
子流程输入映射；路由审计快照钉取值；dry-run 模拟面注入占位形状。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.definitions.dry_run import simulate_route
from flowork.definitions.node import MultiInstance
from flowork.engine.engine import ProcessEngine


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _open(engine: ProcessEngine, instance_id: str, activity: str | None = None) -> list:
    tasks = engine.task.list_tasks(instance_id=instance_id)
    return [t for t in tasks if activity is None or t.activity_code == activity]


# —— 条件位：business_key 驱动分支；started_at 参与时长运算 ————


def test_business_key_condition_routes(engine: ProcessEngine):
    definition = (
        FlowBuilder("inst_bk")
        .start()
        .branch("gate")
        .user_task("vip", assignee="'u_vip'")
        .user_task("normal", assignee="'u_normal'")
        .end()
        .connect("start", "gate")
        .flow("gate", "vip", condition="instance.business_key == 'VIP-001'")
        .flow("gate", "normal", default=True)
        .connect("vip", "end")
        .connect("normal", "end")
        .build()
    )
    engine.repository.deploy(definition)

    hot = engine.runtime.start_process("inst_bk", start_user="u_s", business_key="VIP-001")
    assert [t.activity_code for t in _open(engine, hot)] == ["vip"]

    plain = engine.runtime.start_process("inst_bk", start_user="u_s")
    assert [t.activity_code for t in _open(engine, plain)] == ["normal"]


def test_started_at_supports_duration_arithmetic(engine: ProcessEngine):
    """刚发起的实例：now - started_at 为极短时长 → 命中 fresh 分支。"""
    definition = (
        FlowBuilder("inst_dur")
        .start()
        .branch("gate")
        .user_task("fresh", assignee="'u_f'")
        .user_task("stale", assignee="'u_s2'")
        .end()
        .connect("start", "gate")
        .flow("gate", "fresh", condition="now - instance.started_at < duration('10m')")
        .flow("gate", "stale", default=True)
        .connect("fresh", "end")
        .connect("stale", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("inst_dur", start_user="u_s", business_key="BK-7")
    assert [t.activity_code for t in _open(engine, iid)] == ["fresh"]


# —— 取人位：business_key 参与取人表达式 ————


def test_business_key_in_assignee_expression(engine: ProcessEngine):
    definition = (
        FlowBuilder("inst_subj")
        .start()
        .user_task("handler", assignee="'u_' + instance.business_key")
        .end()
        .connect("start", "handler")
        .connect("handler", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("inst_subj", start_user="u_s", business_key="ops")
    tasks = _open(engine, iid, "handler")
    assert len(tasks) == 1 and tasks[0].assignee == "u_ops"


# —— 会签出口位：instance.* 与 task.* 计数组合 ————


def test_exit_formula_uses_instance(engine: ProcessEngine):
    """FAST 绿色通道：出口公式进入即真 → 零票完成；NORMAL 侧按票数门槛。"""
    definition = (
        FlowBuilder("inst_exit")
        .start()
        .user_task(
            "voting",
            assignees=["'u_a'", "'u_b'", "'u_c'"],
            multi_instance=MultiInstance(
                mode="parallel",
                approve_exit="task.approve_count >= 2 || instance.business_key == 'FAST'",
            ),
        )
        .end()
        .connect("start", "voting")
        .connect("voting", "end")
        .build()
    )
    engine.repository.deploy(definition)
    fast = engine.runtime.start_process("inst_exit", start_user="u_s", business_key="FAST")
    assert engine.runtime.get_instance(fast).state == "completed"  # 进入即出，零待办
    assert _open(engine, fast) == []

    slow = engine.runtime.start_process("inst_exit", start_user="u_s", business_key="NORMAL")
    engine.task.complete(_open(engine, slow, "voting")[0].id, actor="u_a")
    assert len(_open(engine, slow, "voting")) == 2  # 1/3 未达门槛，余两票在途
    engine.task.complete(_open(engine, slow, "voting")[0].id, actor="u_b")
    assert engine.runtime.get_instance(slow).state == "completed"


# —— 子流程输入映射位：父 business_key 携带进子上下文 ————


def test_subprocess_mapping_carries_business_key(engine: ProcessEngine):
    child = (
        FlowBuilder("inst_child")
        .start()
        .user_task("check", assignee="'u_' + parent_ref")
        .end()
        .connect("start", "check")
        .connect("check", "end")
        .build()
        .model_copy(update={"variables": [ProcessVariable(name="parent_ref", type="string")]})
    )
    engine.repository.deploy(child)
    parent = (
        FlowBuilder("inst_parent")
        .start()
        .call_activity(
            "sub", process_code="inst_child", input_mappings={"parent_ref": "instance.business_key"}
        )
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    engine.repository.deploy(parent)
    iid = engine.runtime.start_process("inst_parent", start_user="u_s", business_key="TICKET-9")
    tasks = engine.task.list_tasks()  # 全量中找子任务（跨实例）
    check_tasks = [t for t in tasks if t.activity_code == "check"]
    assert len(check_tasks) == 1 and check_tasks[0].assignee == "u_TICKET-9"
    assert _open(engine, iid) == []  # 父停在子流程等待


# —— 路由审计快照：instance 取值钉取 ————


def test_route_audit_snapshot_contains_instance(engine: ProcessEngine):
    definition = (
        FlowBuilder("inst_audit")
        .start()
        .branch("gate")
        .user_task("vip", assignee="'u_vip'")
        .user_task("normal", assignee="'u_normal'")
        .end()
        .connect("start", "gate")
        .flow("gate", "vip", condition="instance.business_key == 'VIP-001'")
        .flow("gate", "normal", default=True)
        .connect("vip", "end")
        .connect("normal", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("inst_audit", start_user="u_s", business_key="VIP-001")

    decisions = [d for d in engine.history.route_decisions(iid) if d.source_activity == "gate"]
    assert len(decisions) == 1
    snapshot = decisions[0].snapshot
    assert snapshot["instance"]["id"] == iid
    assert snapshot["instance"]["business_key"] == "VIP-001"
    assert "started_at" in snapshot["instance"]  # datetime 已 ISO 化


# —— dry-run 模拟面：占位形状（无实例） ————


def test_dry_run_instance_placeholder_satisfies_condition():
    definition = (
        FlowBuilder("inst_dry")
        .start()
        .branch("gate")
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "gate")
        .flow("gate", "a", condition="instance.id == 'i_preview'")
        .flow("gate", "b", default=True)
        .connect("a", "end")
        .connect("b", "end")
        .build()
    )
    result = simulate_route(definition, {})
    assert "a" in [step.node for step in result.steps]
