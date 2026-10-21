"""P1-1（kernel-v2 spec §7/§16）：消息服务测试——缓冲、TTL、关联、竞态。

验收：消息先到竞态——先发布的消息入缓冲，订阅出现时按先来先服务关联；
message_id 防重；TTL 过期不关联且可清理。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from flowork.clock import utc_now
from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import (
    EventOutboxRow,
    MessageBufferRow,
    MessageSubscriptionRow,
)


@pytest.fixture
def engine() -> ProcessEngine:
    engine = ProcessEngine()
    definition = (
        FlowBuilder("msg_flow")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(definition)
    return engine


def _subscribe(engine: ProcessEngine, instance_id: str, key: str = "ORD-1") -> None:
    token = next(t for t in engine.history.tokens(instance_id) if t.state == "unfinished")
    engine.messages.subscribe(instance_id, token.id, token.activity_code, "order_paid", key)


def test_message_buffered_then_correlated_on_subscribe(engine: ProcessEngine):
    """先到消息进缓冲；订阅出现即关联（载荷合并进实例变量）。"""
    instance_id = engine.runtime.start_process("msg_flow", start_user="u_s")
    assert engine.messages.publish_message("order_paid", "ORD-1", {"paid": True}) is False
    with engine.session_factory() as session:  # 已入缓冲
        assert len(session.scalars(select(MessageBufferRow)).all()) == 1

    _subscribe(engine, instance_id)
    instance = engine.runtime.get_instance(instance_id)
    assert instance.state == "running"
    with engine.session_factory() as session:
        subs = session.scalars(select(MessageSubscriptionRow)).all()
        assert [s.state for s in subs] == ["matched"]
        assert session.scalars(select(MessageBufferRow)).all() == []  # 缓冲已消费


def test_message_with_open_subscription_correlates_immediately(engine: ProcessEngine):
    instance_id = engine.runtime.start_process("msg_flow", start_user="u_s")
    _subscribe(engine, instance_id)
    assert engine.messages.publish_message("order_paid", "ORD-1", {"paid": True}) is True
    assert engine.runtime.get_instance(instance_id).state == "running"
    with engine.session_factory() as session:
        assert session.scalars(select(MessageBufferRow)).all() == []


def test_message_id_dedup(engine: ProcessEngine):
    engine.runtime.start_process("msg_flow", start_user="u_s")
    engine.messages.publish_message("order_paid", "ORD-1", {"n": 1}, message_id="m-1")
    again = engine.messages.publish_message("order_paid", "ORD-1", {"n": 2}, message_id="m-1")
    assert again is False
    with engine.session_factory() as session:
        rows = session.scalars(select(MessageBufferRow)).all()
        assert len(rows) == 1 and rows[0].payload == {"n": 1}  # 重复被忽略


def test_expired_message_not_correlated(engine: ProcessEngine):
    instance_id = engine.runtime.start_process("msg_flow", start_user="u_s")
    engine.messages.publish_message("order_paid", "ORD-1", {"paid": True}, ttl_seconds=0)
    stale = utc_now() - timedelta(seconds=1)
    with engine.session_factory() as session, session.begin():
        for row in session.scalars(select(MessageBufferRow)).all():
            row.expires_at = stale
    _subscribe(engine, instance_id)
    with engine.session_factory() as session:
        subs = session.scalars(select(MessageSubscriptionRow)).all()
        assert [s.state for s in subs] == ["open"]  # 过期消息不关联


def test_correlation_key_mismatch_leaves_both_sides(engine: ProcessEngine):
    instance_id = engine.runtime.start_process("msg_flow", start_user="u_s")
    engine.messages.publish_message("order_paid", "ORD-2", {"paid": True})  # 键不匹配
    _subscribe(engine, instance_id, key="ORD-1")
    with engine.session_factory() as session:
        assert len(session.scalars(select(MessageBufferRow)).all()) == 1
        assert session.scalars(select(MessageSubscriptionRow)).first().state == "open"


def test_race_first_come_first_served_single_correlation(engine: ProcessEngine):
    """验收（P1-1）：同键多订阅竞态——先来先服务，只关联一个，其余保持 open。"""
    first = engine.runtime.start_process("msg_flow", start_user="u_1")
    second = engine.runtime.start_process("msg_flow", start_user="u_2")
    _subscribe(engine, first)
    _subscribe(engine, second)
    assert engine.messages.publish_message("order_paid", "ORD-1", {"paid": True}) is True
    with engine.session_factory() as session:
        states = sorted(s.state for s in session.scalars(select(MessageSubscriptionRow)).all())
    assert states == ["matched", "open"]  # 恰一个关联


def test_correlation_respects_input_contract(engine: ProcessEngine):
    """关联写入与人工任务完成同一条路径：走 input 契约 node_output 边界。"""
    definition = engine.repository.get_definition("msg_flow")
    definition.input = {
        "type": "object",
        "properties": {"amount": {"type": "number"}},
        "required": ["amount"],
    }
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process(
        "msg_flow", start_user="u_s", variables={"amount": 1}
    )
    _subscribe(engine, instance_id)
    from flowork.definitions.contract import InputContractError

    with pytest.raises(InputContractError, match="node_output"):
        engine.messages.publish_message("order_paid", "ORD-1", {"amount": "污染"})


def test_signal_wakes_all_waiting_subscriptions(engine: ProcessEngine):
    instance_id = engine.runtime.start_process("msg_flow", start_user="u_s")
    _subscribe(engine, instance_id)
    assert engine.messages.signal("order_paid") == 1
    assert engine.messages.signal("order_paid") == 0  # 无 open 订阅
    with engine.session_factory() as session:
        assert session.scalars(select(MessageSubscriptionRow)).first().state == "matched"


def test_events_recorded(engine: ProcessEngine):
    instance_id = engine.runtime.start_process("msg_flow", start_user="u_s")
    _subscribe(engine, instance_id)
    engine.messages.publish_message("order_paid", "ORD-1", {"paid": True})
    with engine.session_factory() as session:
        names = {row.event_name for row in session.scalars(select(EventOutboxRow)).all()}
    assert DomainEvent.MESSAGE_CORRELATED.value in names
    _ = instance_id


def test_second_event_same_key_with_new_message_id_correlates(engine: ProcessEngine):
    """REVIEW P0-②回归：防重只按 message_id——同一业务键的第二次事件（新 id）
    对新 open 订阅照常关联，不被 (name,key) 维度的旧 matched 订阅误杀。"""
    first_instance = engine.runtime.start_process("msg_flow", start_user="u_1")
    _subscribe(engine, first_instance)  # 第一轮等待
    assert (
        engine.messages.publish_message("order_paid", "ORD-1", {"seq": 1}, message_id="m-1") is True
    )  # 关联 → matched（定格 m-1）

    second_instance = engine.runtime.start_process("msg_flow", start_user="u_2")
    _subscribe(engine, second_instance)  # 第二轮等待同一业务单的更新
    assert (
        engine.messages.publish_message("order_paid", "ORD-1", {"seq": 2}, message_id="m-2") is True
    )  # 新事件照常关联
    assert engine.runtime.get_instance(second_instance).state == "running"


def test_consumed_message_id_replay_is_ignored(engine: ProcessEngine):
    """REVIEW P0-②回归：已消费 message_id 重放被订阅定格记录拦下（不入库不关联）。"""
    instance_id = engine.runtime.start_process("msg_flow", start_user="u_s")
    _subscribe(engine, instance_id)
    assert (
        engine.messages.publish_message("order_paid", "ORD-1", {"seq": 1}, message_id="m-replay")
        is True
    )
    # 重放同一 message_id（此时无 open 订阅）——不得再入缓冲
    assert (
        engine.messages.publish_message("order_paid", "ORD-1", {"seq": 1}, message_id="m-replay")
        is False
    )
    with engine.session_factory() as session:
        assert session.scalars(select(MessageBufferRow)).all() == []


def test_remove_instance_keeps_message_id_dedupe_memory():
    """remove_instance 保留已消费订阅行＝message_id 防重集（2026-10-21 内核 review P2）。

    修前整删订阅行：remove 后同 id 重发被重新入缓冲，下一个等待者
    二次消费同一逻辑消息（违背「message_id 全局唯一防重」注释承诺）。
    """
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("rmv_dedupe")
        .start()
        .wait("w", message={"name": "evt"})
        .user_task("after", assignee="'u1'")
        .end()
        .connect("start", "w")
        .connect("w", "after")
        .connect("after", "end")
        .build()
    )
    iid = engine.runtime.start_process("rmv_dedupe", start_user="u_s")
    assert engine.messages.publish_message("evt", payload={}, message_id="m-dedupe-1") is True
    task = engine.task.list_tasks(instance_id=iid)[0]
    engine.task.complete(task.id, actor="u1")
    assert engine.runtime.get_instance(iid).state == "completed"
    engine.task.remove_instance(iid, actor="ops")
    # 同 id 重发：matched 订阅行仍在（防重集随实例删除被蒸发是修前缺陷），
    # 不得再次入缓冲给下一个等待者二次消费
    engine.messages.publish_message("evt", payload={}, message_id="m-dedupe-1")
    rebuffed = [
        m
        for m in engine.messages.list_messages(name="evt")
        if m.get("kind") == "buffer" and m.get("message_id") == "m-dedupe-1"
    ]
    assert not rebuffed, f"同 id 消息被重新入缓冲：{rebuffed}"
