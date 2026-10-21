"""模拟虚拟时钟（批 2）：推钟兑现 deadline / 等待到时 / 实例时限。

模拟域的定时 Job 置 virtual（真实调度器不可见），controls.clock 步进虚拟
时刻后按 run_at 用**真实策略面**兑现（jobs.strategies 同源零第二实现）。
拍板③＝全兑现：notify 留痕、reassign 走改派语义、auto_approve/reject 走
对应出口、实例时限落终态。证据面＝notes（事件面对模拟静默）与已办行。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import InstanceDeadline
from flowork.definitions.node import MultiInstance, TaskDeadline
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from flowork.persistence.orm import JobRow, TaskHistoryRow, TaskRow


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _deploy(engine: ProcessEngine, flow: FlowBuilder) -> None:
    engine.repository.deploy(flow.build())


def test_clock_fires_wait_timeout_to_elapsed(engine: ProcessEngine):
    """等待到时经虚拟时钟兑现：推过 after_minutes → elapsed 分支。"""
    engine.repository.deploy(
        FlowBuilder("clk_wait")
        .start()
        .wait(
            "w",
            message={"name": "paid", "correlation_key": "'K'"},
            timeout={"after_minutes": 60},
        )
        .user_task("ok", assignee="'u_a'")
        .user_task("late", assignee="'u_b'")
        .end()
        .connect("start", "w")
        .flow("w", "ok", condition='wait.result == "matched"')
        .flow("w", "late", condition='wait.result == "elapsed"')
        .connect("ok", "end")
        .connect("late", "end")
        .build()
    )
    report = engine.runtime.simulate("clk_wait", controls={"clock": [{"advance_minutes": 120}]})
    assert report.final_state == "completed"
    assert report.visited == ["start", "w", "late", "end"]
    assert report.advanced_minutes == 120
    assert any("wait_timeout@w" in note for note in report.notes)


def test_clock_advance_to_next_event(engine: ProcessEngine):
    """推进到下一事件：直达最近 run_at。"""
    engine.repository.deploy(
        FlowBuilder("clk_next")
        .start()
        .wait(
            "w",
            message={"name": "paid", "correlation_key": "'K'"},
            timeout={"after_minutes": 60},
        )
        .user_task("late", assignee="'u_b'")
        .end()
        .connect("start", "w")
        .flow("w", "late", condition='wait.result == "elapsed"')
        .flow("w", "end", default=True)
        .connect("late", "end")
        .build()
    )
    report = engine.runtime.simulate(
        "clk_next", controls={"clock": [{"advance_to_next_event": True}]}
    )
    assert report.final_state == "completed"
    assert "late" in report.visited
    assert any("推进到下一事件" in note for note in report.notes)


def test_clock_next_event_without_timers_notes_and_finishes(engine: ProcessEngine):
    """无后续定时事件：如实注记、时刻不变，流程照常收束。"""
    engine.repository.deploy(
        FlowBuilder("clk_empty")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    report = engine.runtime.simulate(
        "clk_empty",
        controls={"decisions": [], "clock": [{"advance_to_next_event": True}]},
    )
    assert report.final_state == "running"
    assert any("无后续定时事件" in note for note in report.notes)
    assert [p["node"] for p in report.pending] == ["a"]


def test_clock_deadline_reassign_full_semantics(engine: ProcessEngine):
    """48h 未办→转上级（拍板③全兑现）：真实 reassign 策略＝转办语义+票随人走。"""
    engine.repository.deploy(
        FlowBuilder("clk_reassign")
        .start()
        .user_task(
            "a",
            assignee="'u_a'",
            deadlines=[TaskDeadline(after_minutes=2880, action="reassign", reassign_to="'u_boss'")],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    report = engine.runtime.simulate(
        "clk_reassign",
        controls={
            "decisions": [{"node": "a", "by": "u_boss", "action": "complete"}],
            "clock": [{"advance_minutes": 2880}],
        },
    )
    assert report.final_state == "completed"
    with engine.session_factory() as session:
        rows = session.scalars(
            select(TaskHistoryRow).where(TaskHistoryRow.instance_id == report.instance_id)
        ).all()
    transfer = next(r for r in rows if r.outcome == "transfer")
    assert transfer.acted_by == "system:timeout"  # 真实策略面执行的归因
    assert any(r.assignee == "u_boss" and r.outcome == "agree" for r in rows)
    assert report.advanced_minutes == 2880
    assert any("task_deadline@a" in note for note in report.notes)


def test_clock_deadline_auto_approve(engine: ProcessEngine):
    """超时自动通过：策略面 complete_in_ctx（actor=system:timeout）。"""
    engine.repository.deploy(
        FlowBuilder("clk_auto")
        .start()
        .user_task(
            "a", assignee="'u_a'", deadlines=[TaskDeadline(after_minutes=30, action="auto_approve")]
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    report = engine.runtime.simulate(
        "clk_auto", controls={"decisions": [], "clock": [{"advance_minutes": 60}]}
    )
    assert report.final_state == "completed"
    with engine.session_factory() as session:
        row = session.scalars(
            select(TaskHistoryRow).where(TaskHistoryRow.instance_id == report.instance_id)
        ).one()
    assert row.outcome == "agree" and row.acted_by == "system:timeout"


def test_clock_deadline_notify_keeps_task_pending(engine: ProcessEngine):
    """notify 只预警不办结：任务仍在，时钟耗尽后停待决策（诚实停点）。"""
    engine.repository.deploy(
        FlowBuilder("clk_notify")
        .start()
        .user_task(
            "a", assignee="'u_a'", deadlines=[TaskDeadline(after_minutes=30, action="notify")]
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    report = engine.runtime.simulate(
        "clk_notify", controls={"decisions": [], "clock": [{"advance_minutes": 60}]}
    )
    assert report.final_state == "running"
    assert [p["node"] for p in report.pending] == ["a"]
    assert any("strategy=notify" in note for note in report.notes)
    with engine.session_factory() as session:
        task = session.scalars(
            select(TaskRow).where(TaskRow.instance_id == report.instance_id)
        ).one()
    assert task.state == "ready"  # notify 不动任务


def test_clock_instance_deadline_cancels(engine: ProcessEngine):
    """实例时限到点：真实 cancel 策略落终态（级联撤待办）。"""
    flow = (
        FlowBuilder("clk_inst")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
        .model_copy(update={"deadline": InstanceDeadline(after_minutes=60, strategy="cancel")})
    )
    engine.repository.deploy(flow)
    report = engine.runtime.simulate(
        "clk_inst", controls={"decisions": [], "clock": [{"advance_minutes": 120}]}
    )
    assert report.final_state == "cancelled"
    assert any("instance_deadline" in note for note in report.notes)


def test_clock_waits_assumption_precedes_tick(engine: ProcessEngine):
    """显式等待假设优先：matched 直接兑现（时钟未被消费）。"""
    engine.repository.deploy(
        FlowBuilder("clk_assume")
        .start()
        .wait(
            "w",
            message={"name": "paid", "correlation_key": "'K'"},
            timeout={"after_minutes": 60},
        )
        .user_task("ok", assignee="'u_a'")
        .end()
        .connect("start", "w")
        .flow("w", "ok", condition='wait.result == "matched"')
        .flow("w", "end", default=True)
        .connect("ok", "end")
        .build()
    )
    report = engine.runtime.simulate(
        "clk_assume",
        controls={"waits": {"w": "matched"}, "clock": [{"advance_minutes": 999}]},
    )
    assert report.final_state == "completed"
    assert "ok" in report.visited
    assert report.advanced_minutes == 0


def test_clock_carried_jobs_stay_virtual(engine: ProcessEngine):
    """串行换棒 carry 的剩余档在模拟域仍是 virtual（不漏给真实调度器）。"""
    engine.repository.deploy(
        FlowBuilder("clk_carry")
        .start()
        .user_task(
            "cs",
            assignees=["'u_a'", "'u_b'"],
            multi_instance=MultiInstance(mode="sequential"),
            deadlines=[TaskDeadline(after_minutes=60, action="notify")],
        )
        .end()
        .connect("start", "cs")
        .connect("cs", "end")
        .build()
    )
    report = engine.runtime.simulate("clk_carry")
    assert report.final_state == "completed"
    with engine.session_factory() as session:
        states = {
            job.state
            for job in session.scalars(
                select(JobRow).where(JobRow.instance_id == report.instance_id)
            ).all()
        }
    assert states <= {"virtual", "done"}
    assert "scheduled" not in states


def test_clock_parse_rejects_junk():
    """时钟步骤校验：负数/未知形状报 FloworkError。"""
    from flowork.engine.simulation_controls import parse_controls

    with pytest.raises(FloworkError, match="advance_minutes"):
        parse_controls({"clock": [{"advance_minutes": -5}]})
    with pytest.raises(FloworkError, match="时钟步骤"):
        parse_controls({"clock": [{"warp": True}]})


def test_clock_consult_deferral_does_not_spin():
    """咨询推迟不烧步数（2026-10-21 内核 review P1-6 回归）。

    修前推迟的 Job 对驱动循环仍是「已兑现」→ continue → 同一 Job 反复入选
    直到 500 步上限，咨询待办永远排不上、notes 里 500 条同样的兑现记录。
    现推迟返回 False：决策/机器人先推进，咨询交回后推钟再兑。
    """
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("clk_consult")
        .start()
        .user_task(
            "m",
            assignee="'u1'",
            deadlines=[TaskDeadline(after_minutes=30, action="auto_approve")],
        )
        .end()
        .connect("start", "m")
        .connect("m", "end")
        .build()
    )
    from flowork.engine.simulation_runner import drive_simulation
    from flowork.engine.simulation_start import start_simulation

    iid, controls = start_simulation(
        engine,
        "clk_consult",
        # decisions=[] 禁用机器人：咨询保持未交回，推钟才会撞上「咨询期推迟」
        controls={"decisions": [], "clock": [{"advance_minutes": 60}]},
    )
    task = engine.task.list_tasks(instance_id=iid)[0]
    engine.task.consult(task.id, ["u2"], actor="u1")  # 咨询未交回＝deadline 推迟

    notes, _advanced = drive_simulation(engine, iid, controls)

    defer_notes = [n for n in notes if "推迟" in n]
    assert defer_notes, "推迟事实应留痕（修前谎称已兑现）"
    assert len(notes) < 10, f"步数被烧穿：{len(notes)} 条"
    # 咨询待办不被烧掉的步数吞掉——仍是待决策点（诚实停点而非假循环）
    pending = [t.activity_code for t in engine.task.list_tasks(instance_id=iid)]
    assert "m" in pending or len(pending) >= 1, pending
