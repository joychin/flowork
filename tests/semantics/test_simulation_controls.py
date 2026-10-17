"""模拟控制面（批 1b）：决策全量词表 / 桩 / 等待假设 / 待决策点。

决策＝按序扮演办理人（与演练剧本同构的十词表），全部经真实任务动作面——
转办票随人走、驳回复活圈定最近一轮、会签计数都在模拟里原生成立（全量
决策词拍板的价值钉子）。桩＝替身 handler 同一写入口注入变量；等待假设＝
matched（可带载荷，走 output_mappings 同语义）/elapsed。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.definitions.wait_node import WaitMessage
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from flowork.persistence.orm import TaskHistoryRow


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _deploy(
    engine: ProcessEngine, flow: FlowBuilder, *, variables: list[ProcessVariable] | None = None
) -> None:
    built = flow.build()
    if variables:
        built = built.model_copy(update={"variables": variables})
    engine.repository.deploy(built)


def test_decisions_reject_loop_with_revival(engine: ProcessEngine):
    """驳回回退→重报→通过：复活圈定最近一轮在模拟里原生成立。"""
    _deploy(
        engine,
        FlowBuilder("ctl_reject")
        .start()
        .user_task("submit", assignee="'u_zhang'")
        .user_task("manager", assignee="'u_manager'", reject_target="submit")
        .end()
        .connect("start", "submit")
        .connect("submit", "manager")
        .connect("manager", "end"),
    )
    report = engine.runtime.simulate(
        "ctl_reject",
        controls={
            "decisions": [
                {"node": "submit", "by": "u_zhang", "action": "complete", "variables": {"n": 1}},
                {"node": "manager", "by": "u_manager", "action": "reject", "comment": "退回补充"},
                {"node": "submit", "by": "u_zhang", "action": "complete"},
                {"node": "manager", "by": "u_manager", "action": "complete"},
            ]
        },
    )
    assert report.final_state == "completed"
    assert report.visited == ["start", "submit", "manager", "submit", "manager", "end"]
    assert [(d["node"], d["acted_by"], d["outcome"]) for d in report.decisions] == [
        ("submit", "u_zhang", "agree"),
        ("manager", "u_manager", "reject"),
        ("submit", "u_zhang", "agree"),
        ("manager", "u_manager", "agree"),
    ]


def test_decisions_transfer_vote_follows_person(engine: ProcessEngine):
    """转办票随人走：原办理人 transfer 出局、被转办人 agree 计票。"""
    _deploy(
        engine,
        FlowBuilder("ctl_transfer")
        .start()
        .user_task("finance", assignee="'u_finance'")
        .end()
        .connect("start", "finance")
        .connect("finance", "end"),
    )
    report = engine.runtime.simulate(
        "ctl_transfer",
        controls={
            "decisions": [
                {"node": "finance", "action": "transfer", "to": "u_bak", "comment": "交接"},
                {"node": "finance", "by": "u_bak", "action": "complete"},
            ]
        },
    )
    assert report.final_state == "completed"
    with engine.session_factory() as session:
        rows = session.scalars(
            select(TaskHistoryRow).where(TaskHistoryRow.instance_id == report.instance_id)
        ).all()
    assert {(r.assignee, r.outcome) for r in rows} == {
        ("u_finance", "transfer"),
        ("u_bak", "agree"),
    }


def test_decisions_pending_decision_point(engine: ProcessEngine):
    """给了决策清单＝禁用机器人：未覆盖的待办停在「待决策点」并进报告。"""
    _deploy(
        engine,
        FlowBuilder("ctl_pending")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end"),
    )
    report = engine.runtime.simulate(
        "ctl_pending", controls={"decisions": [{"node": "a", "action": "transfer", "to": "u_b"}]}
    )
    assert report.final_state == "running"
    assert [(p["node"], p["assignee"]) for p in report.pending] == [("a", "u_b")]
    assert any("待决策" in note for note in report.notes)


def test_stub_values_drive_downstream_branch(engine: ProcessEngine):
    """桩＝替身 handler 写变量：下游条件按真实语义求值（分支可验）。"""
    _deploy(
        engine,
        FlowBuilder("ctl_stub")
        .start()
        .service_task("score", action="NEVER_REGISTERED")
        .user_task("high", assignee="'u_h'")
        .end()
        .connect("start", "score")
        .flow("score", "high", condition="score_val > 50")
        .flow("score", "end", default=True)
        .connect("high", "end"),
        variables=[ProcessVariable(name="score_val", type="number")],
    )
    report = engine.runtime.simulate("ctl_stub", controls={"stubs": {"score": {"score_val": 99}}})
    assert report.final_state == "completed"
    assert report.visited == ["start", "score", "high", "end"]
    assert report.stubs_applied == ["score"]
    assert report.stubbed == ["score"]  # 到访即桩（值是否注入是另一维）


def test_wait_assumption_matched_and_elapsed(engine: ProcessEngine):
    """等待假设：matched/elapsed 各走一支；无假设＝冻结卡点。"""
    _deploy(
        engine,
        FlowBuilder("ctl_wait")
        .start()
        .wait("w", message=WaitMessage(name="paid", correlation_key="'K'"))
        .user_task("ok", assignee="'u_a'")
        .user_task("late", assignee="'u_b'")
        .end()
        .connect("start", "w")
        .flow("w", "ok", condition='wait.result == "matched"')
        .flow("w", "late", condition='wait.result == "elapsed"')
        .connect("ok", "end")
        .connect("late", "end"),
    )
    matched = engine.runtime.simulate("ctl_wait", controls={"waits": {"w": "matched"}})
    assert matched.final_state == "completed"
    assert matched.visited == ["start", "w", "ok", "end"]
    elapsed = engine.runtime.simulate("ctl_wait", controls={"waits": {"w": {"result": "elapsed"}}})
    assert elapsed.visited == ["start", "w", "late", "end"]
    frozen = engine.runtime.simulate("ctl_wait")
    assert frozen.final_state == "running" and frozen.stalled_at == "w"


def test_wait_matched_payload_applies_output_mappings(engine: ProcessEngine):
    """matched 载荷与消息路径同语义：output_mappings 求值写入实例变量。"""
    _deploy(
        engine,
        FlowBuilder("ctl_payload")
        .start()
        .wait(
            "w",
            message=WaitMessage(name="evt", correlation_key="'K'"),
            output_mappings={"amt": "payload.amount"},
        )
        .user_task("big", assignee="'u_a'")
        .end()
        .connect("start", "w")
        .flow("w", "big", condition="amt > 10")
        .flow("w", "end", default=True)
        .connect("big", "end"),
        variables=[ProcessVariable(name="amt", type="number")],
    )
    report = engine.runtime.simulate(
        "ctl_payload",
        controls={"waits": {"w": {"result": "matched", "payload": {"amount": 50}}}},
    )
    assert report.final_state == "completed"
    assert report.visited == ["start", "w", "big", "end"]
    assert engine.runtime.variables_of(report.instance_id)["amt"] == 50


def test_decisions_address_child_instance_tasks(engine: ProcessEngine):
    """决策按实例全树寻址：子流程节点直接给决策（真实子实例上办理）。"""
    _deploy(
        engine,
        FlowBuilder("ctl_child")
        .start()
        .user_task("c1", assignee="'u_c'")
        .end()
        .connect("start", "c1")
        .connect("c1", "end"),
    )
    _deploy(
        engine,
        FlowBuilder("ctl_parent")
        .start()
        .user_task("p1", assignee="'u_p'")
        .call_activity("sub", process_code="ctl_child")
        .end()
        .connect("start", "p1")
        .connect("p1", "sub")
        .connect("sub", "end"),
    )
    report = engine.runtime.simulate(
        "ctl_parent",
        controls={
            "decisions": [
                {"node": "p1", "action": "complete"},
                {"node": "c1", "by": "u_c", "action": "complete"},
            ]
        },
    )
    assert report.final_state == "completed"
    assert [d["node"] for d in report.decisions] == ["p1", "c1"]


def test_robot_when_no_decisions_given(engine: ProcessEngine):
    """无决策清单＝机器人全通过（基座语义兼容）：办理人=simulator。"""
    _deploy(
        engine,
        FlowBuilder("ctl_robot")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end"),
    )
    report = engine.runtime.simulate("ctl_robot")
    assert report.final_state == "completed"
    assert [(d["node"], d["acted_by"]) for d in report.decisions] == [("a", "simulator")]


def test_parse_controls_rejects_junk(engine: ProcessEngine):
    """控制面校验：未知动作/坏形状报 FloworkError（web 层 422 的内核依据）。"""
    with pytest.raises(FloworkError, match="决策动作未知"):
        engine.runtime.simulate(
            "whatever",
            spec={"code": "x"},
            controls={"decisions": [{"node": "a", "action": "hack"}]},
        )
    with pytest.raises(FloworkError, match="matched/elapsed"):
        engine.runtime.simulate(
            "whatever", spec={"code": "x"}, controls={"waits": {"w": "someday"}}
        )
    with pytest.raises(FloworkError, match="变量对象"):
        engine.runtime.simulate(
            "whatever", spec={"code": "x"}, controls={"stubs": {"s": "not-a-dict"}}
        )
