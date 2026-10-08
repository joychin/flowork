"""消息关联、暂停冻结计时、技术故障二分与 resolve 五动作。"""

from __future__ import annotations

import pytest

from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from tests.suite.features import covers, covers_node
from tests.suite.walkthrough import complete_of, expect_position


def start_message(engine: ProcessEngine, amount: float = 100) -> str:
    return engine.runtime.start_process(
        "suite_message", start_user="u_buyer", variables={"amount": amount}
    )


def subscribe(engine: ProcessEngine, iid: str, name: str, key: str) -> bool:
    """对当前未完令牌登记订阅；命中缓冲返回 True。"""
    token = next(t for t in engine.history.tokens(iid) if t.state == "unfinished")
    return engine.messages.subscribe(iid, token.id, token.activity_code, name, key)


@covers("message.buffered_first", "message.correlate")
@covers_node("suite_message", "start", "await_paid", "echo", "message_end")
def test_message_buffered_then_correlated_on_subscribe(suite_engine: ProcessEngine) -> None:
    """消息先到入缓冲；订阅出现即关联，载荷合并进实例变量。"""
    engine = suite_engine
    assert engine.messages.publish_message("order_paid", "EXP-1", {"paid": True}) is False

    iid = start_message(engine)
    expect_position(engine, iid, nodes={"await_paid"}, tasks={("await_paid", "u_buyer")})
    assert subscribe(engine, iid, "order_paid", "EXP-1"), "订阅命中缓冲立即关联"

    complete_of(engine, iid, "u_buyer")
    expect_position(engine, iid, nodes=set(), state="completed")
    assert engine.history.archive_instance(iid)["instance"]["variables"]["paid"] is True
    names = [e.event_name for e in engine.history.events(iid)]
    assert "message_correlated" in names


@covers("message.correlate")
def test_open_subscription_correlates_immediately(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = start_message(engine)
    subscribe(engine, iid, "order_paid", "EXP-2")
    assert engine.messages.publish_message("order_paid", "EXP-2", {"paid": True}) is True


@covers("message.dedup")
def test_message_id_dedup(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    assert (
        engine.messages.publish_message("order_paid", "EXP-3", {"n": 1}, message_id="m-1") is False
    )
    assert (
        engine.messages.publish_message("order_paid", "EXP-3", {"n": 2}, message_id="m-1") is False
    )


@covers("message.signal_broadcast")
def test_signal_wakes_all_waiting_subscriptions(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    first = start_message(engine)
    second = start_message(engine)
    subscribe(engine, first, "stock_sync", "K-1")
    subscribe(engine, second, "stock_sync", "K-2")
    assert engine.messages.signal("stock_sync") == 2


@covers("lifecycle.pause_resume")
@covers_node("suite_lifecycle", "start", "first_task", "second_task", "lifecycle_end")
def test_pause_freezes_timer_until_resumed(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = engine.runtime.start_process("suite_lifecycle", start_user="u_dev_wu")

    engine.runtime.pause_process(iid, actor="u_cto", reason="月末冻结")
    engine.config.now.advance(60)  # type: ignore[attr-defined]
    engine.jobs.tick()
    names = [e.event_name for e in engine.history.events(iid)]
    assert "timeout_warned" not in names, "暂停期 timer 冻结不触发"

    engine.runtime.resume_process(iid, actor="u_cto")
    engine.config.now.advance(60)  # type: ignore[attr-defined]
    engine.jobs.tick()
    names = [e.event_name for e in engine.history.events(iid)]
    assert names.count("timeout_warned") == 1, "恢复后计时顺延补触发一次"

    complete_of(engine, iid, "u_dev_wu")
    complete_of(engine, iid, "u_cto")
    expect_position(engine, iid, nodes=set(), state="completed")


# —— 技术故障二分（fragile 世界）——


def bring_to_boom(engine: ProcessEngine) -> str:
    iid = engine.runtime.start_process("suite_fragile", start_user="u_dev_wu")
    with pytest.raises(FloworkError):
        complete_of(engine, iid, "u_dev_wu")  # boom 炸：命令回滚 + Incident + suspended
    assert engine.runtime.get_instance(iid).state == "suspended"
    return iid


def open_incident(engine: ProcessEngine, iid: str) -> str:
    incidents = engine.runtime.list_incidents(instance_id=iid)
    assert incidents and incidents[0]["state"] == "open"
    return incidents[0]["id"]


@covers("fault.technical_suspends", "fault.resolve_resume")
@covers_node("suite_fragile", "start", "submit", "boom", "after", "fragile_end")
def test_technical_failure_suspends_then_resume_recurs(suite_engine: ProcessEngine) -> None:
    """handler 异常＝技术故障：挂起+故障单；根因未修时 resume 后复发。"""
    engine = suite_engine
    iid = bring_to_boom(engine)
    engine.runtime.resolve_incident(open_incident(engine, iid), action="resume", actor="ops")
    assert engine.runtime.get_instance(iid).state == "running"

    with pytest.raises(FloworkError):
        complete_of(engine, iid, "u_dev_wu")  # 修复前复发
    assert engine.runtime.get_instance(iid).state == "suspended"


@covers("fault.resolve_retry")
def test_resolve_retry_redoes_after_fix(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = bring_to_boom(engine)

    def explode_fixed(ctx, params):
        ctx.variables["fixed"] = True

    engine.register_handler("explode", explode_fixed, replace=True)
    engine.runtime.resolve_incident(open_incident(engine, iid), action="retry", actor="ops")
    assert engine.runtime.get_instance(iid).state == "running"

    complete_of(engine, iid, "u_dev_wu")
    complete_of(engine, iid, "u_cto")
    expect_position(engine, iid, nodes=set(), state="completed")


@covers("fault.resolve_skip")
def test_resolve_skip_discards_waiting_node_and_moves_on(suite_engine: ProcessEngine) -> None:
    """skip：当前停住节点（失败回滚后停在 submit）的待办作废，修复后直接推进。"""
    engine = suite_engine
    iid = bring_to_boom(engine)

    def explode_fixed(ctx, params):
        ctx.variables["fixed"] = True

    engine.register_handler("explode", explode_fixed, replace=True)
    engine.runtime.resolve_incident(open_incident(engine, iid), action="skip", actor="ops")

    assert engine.task.list_tasks(instance_id=iid, assignee="u_dev_wu") == [], "停住节点待办作废"
    expect_position(
        engine,
        iid,
        nodes={"after"},
        tasks={("after", "u_cto")},
        note="skip 推进越过故障环节",
    )
    complete_of(engine, iid, "u_cto")
    expect_position(engine, iid, nodes=set(), state="completed")


@covers("fault.resolve_cancel")
def test_resolve_cancel_abandons_instance(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = bring_to_boom(engine)
    engine.runtime.resolve_incident(open_incident(engine, iid), action="cancel", actor="ops")
    assert engine.runtime.get_instance(iid).state == "cancelled"
    assert engine.runtime.list_incidents(instance_id=iid)[0]["state"] == "resolved"


@covers("fault.resolve_fail")
def test_resolve_fail_terminates_instance(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = bring_to_boom(engine)
    engine.runtime.resolve_incident(open_incident(engine, iid), action="fail", actor="ops")
    assert engine.runtime.get_instance(iid).state == "terminated"
