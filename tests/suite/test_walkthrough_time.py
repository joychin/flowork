"""逐节点走查·时限族：多档节点超时阶梯推进、超时自动驳回、实例级时限三策略。

时钟由 FakeClock 驱动，每 tick 一档断言一次位置/事件——超时动作也是"走通"的一环。
"""

from __future__ import annotations

from flowork.engine.engine import ProcessEngine
from flowork.events.domain import DomainEvent
from tests.suite.features import covers, covers_node
from tests.suite.walkthrough import complete_of, expect_position


def event_names(engine: ProcessEngine, instance_id: str) -> list[str]:
    return [event.event_name for event in engine.history.events(instance_id)]


@covers("deadline.notify", "deadline.escalate", "deadline.due_at")
@covers_node("suite_deadline_notify", "start", "hold", "notify_end")
def test_notify_then_escalate_walkthrough(suite_engine: ProcessEngine) -> None:
    """预警后升级：任务移交办理人主管，主管人工办结走完。"""
    engine = suite_engine
    iid = engine.runtime.start_process("suite_deadline_notify", start_user="u_dev_liu")
    expect_position(engine, iid, nodes={"hold"}, tasks={("hold", "u_dev_liu")})

    task = engine.task.list_tasks(instance_id=iid)[0]
    assert task.due_at is not None, "首条 deadline 应物化 due_at"

    engine.config.now.advance(5)  # type: ignore[attr-defined]
    assert engine.jobs.tick() == 1
    assert event_names(engine, iid).count("timeout_warned") == 1

    engine.config.now.advance(5)  # type: ignore[attr-defined]
    assert engine.jobs.tick() == 1
    assert DomainEvent.TASK_ESCALATED in event_names(engine, iid)
    expect_position(
        engine,
        iid,
        nodes={"hold"},
        tasks={("hold", "u_lead_fe")},
        note="升级档：任务移交办理人主管（rd_fe 主管）",
    )

    complete_of(engine, iid, "u_lead_fe")
    expect_position(engine, iid, nodes=set(), state="completed")


@covers("deadline.reassign")
@covers_node("suite_deadline_reassign", "start", "gate", "reassign_end")
def test_reassign_moves_task_and_skips_later_auto(suite_engine: ProcessEngine) -> None:
    """改派档移交指定目标；deadline 绑定原任务——移交后其后的 auto 档按已终结跳过。"""
    engine = suite_engine
    iid = engine.runtime.start_process("suite_deadline_reassign", start_user="u_dev_liu")

    engine.config.now.advance(5)  # type: ignore[attr-defined]
    assert engine.jobs.tick() == 1
    expect_position(
        engine,
        iid,
        nodes={"gate"},
        tasks={("gate", "u_lead_be")},
        note="改派档：任务移交 reassign_to 目标",
    )

    engine.config.now.advance(5)  # type: ignore[attr-defined]
    assert engine.jobs.tick() == 1  # auto_approve 档到期被处理，但原任务已终结→跳过
    expect_position(
        engine,
        iid,
        nodes={"gate"},
        tasks={("gate", "u_lead_be")},
        note="auto 档不作用于移交后的新任务：仍待人办",
    )

    complete_of(engine, iid, "u_lead_be")
    expect_position(engine, iid, nodes=set(), state="completed")


@covers("deadline.auto_approve")
@covers_node("suite_deadline_auto", "start", "gate", "auto_end")
def test_auto_approve_finishes_flow(suite_engine: ProcessEngine) -> None:
    """原任务未被移交：预警档后自动通过档生效，节点同意出口、流程走完。"""
    engine = suite_engine
    iid = engine.runtime.start_process("suite_deadline_auto", start_user="u_dev_liu")

    engine.config.now.advance(5)  # type: ignore[attr-defined]
    assert engine.jobs.tick() == 1
    assert event_names(engine, iid).count("timeout_warned") == 1

    engine.config.now.advance(5)  # type: ignore[attr-defined]
    assert engine.jobs.tick() == 1
    expect_position(
        engine, iid, nodes=set(), state="completed", note="auto_approve：超时自动同意并走完"
    )
    rows = [r for r in engine.history.task_history(instance_id=iid) if r.activity_code == "gate"]
    assert any(r.outcome == "agree" and r.acted_by == "system:timeout" for r in rows)


@covers("deadline.auto_reject", "reject.to_start")
@covers_node("suite_deadline_reject", "start", "gate", "reject_end")
def test_timeout_auto_reject_walkthrough(suite_engine: ProcessEngine) -> None:
    """超时自动驳回：闸口一票否决回发起人，重提后人工通过走完。"""
    engine = suite_engine
    iid = engine.runtime.start_process("suite_deadline_reject", start_user="u_dev_liu")
    expect_position(engine, iid, nodes={"gate"}, tasks={("gate", "u_dev_liu")})

    engine.config.now.advance(10)  # type: ignore[attr-defined]
    assert engine.jobs.tick() == 1
    expect_position(
        engine,
        iid,
        nodes={"start"},
        tasks={("start", "u_dev_liu")},
        note="auto_reject 驳回到 start：发起人重提",
    )

    complete_of(engine, iid, "u_dev_liu")
    expect_position(
        engine, iid, nodes={"gate"}, tasks={("gate", "u_dev_liu")}, note="重提再次到闸口"
    )
    complete_of(engine, iid, "u_dev_liu")
    expect_position(engine, iid, nodes=set(), state="completed")


@covers("deadline.instance_mark")
@covers_node("suite_deadline_mark", "start", "hold", "instance_end")
def test_instance_deadline_mark_keeps_running(suite_engine: ProcessEngine) -> None:
    """实例时限 mark 策略：到点打标发事件，实例继续跑。"""
    engine = suite_engine
    iid = engine.runtime.start_process("suite_deadline_mark", start_user="u_dev_wu")
    engine.config.now.advance(60)  # type: ignore[attr-defined]
    assert engine.jobs.tick() == 1
    assert DomainEvent.INSTANCE_DEADLINE_REACHED in event_names(engine, iid)
    expect_position(
        engine,
        iid,
        nodes={"hold"},
        tasks={("hold", "u_dev_wu")},
        state="running",
        note="mark 只打标不拦截",
    )

    complete_of(engine, iid, "u_dev_wu")
    expect_position(engine, iid, nodes=set(), state="completed")


@covers("deadline.instance_cancel")
@covers_node("suite_deadline_cancel", "start", "hold", "instance_end")
def test_instance_deadline_cancel_kills_flow(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = engine.runtime.start_process("suite_deadline_cancel", start_user="u_dev_wu")
    engine.config.now.advance(60)  # type: ignore[attr-defined]
    assert engine.jobs.tick() == 1
    expect_position(engine, iid, nodes=set(), state="cancelled", note="cancel 策略作废实例")
    assert engine.task.list_tasks(instance_id=iid) == []


@covers("deadline.instance_terminate")
@covers_node("suite_deadline_terminate", "start", "hold", "instance_end")
def test_instance_deadline_terminate_kills_flow(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = engine.runtime.start_process("suite_deadline_terminate", start_user="u_dev_wu")
    engine.config.now.advance(60)  # type: ignore[attr-defined]
    assert engine.jobs.tick() == 1
    expect_position(engine, iid, nodes=set(), state="terminated", note="terminate 策略强杀实例")
