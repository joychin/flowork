"""P0-3（kernel-v2 spec §10/§16）：CloudEvents 信封 + outbox 消费器测试。

验收：消费端按 id 幂等（重发不重算）；信封 type/id/source/data 与 outbox 行对齐；
至少一次语义（送达后崩溃＝delivered_at 未提交→重发）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from flowork.clock import utc_now
from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.events.consumer import IdempotentSink, OutboxConsumer
from flowork.persistence.orm import EventOutboxRow


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _run_flow(engine: ProcessEngine) -> str:
    definition = (
        FlowBuilder("evt_flow")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("evt_flow", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a")
    return instance_id


def _event_names(engine: ProcessEngine) -> set[str]:
    with engine.session_factory() as session:
        return {row.event_name for row in session.scalars(select(EventOutboxRow))}


def test_envelope_shape_and_delivery_marks(engine: ProcessEngine):
    instance_id = _run_flow(engine)
    received: list[dict] = []
    consumer = OutboxConsumer(engine.session_factory, received.append, engine_id="eng-1")

    delivered = consumer.poll()
    assert delivered == len(received) > 0
    assert consumer.poll() == 0  # 已标记送达，不重发

    by_type = {env["type"]: env for env in received}
    assert "process_started" in by_type and "task_completed" in by_type
    started = by_type["process_started"]
    assert started["specversion"] == "1.0.2"
    assert started["source"] == "flowork://eng-1"
    assert started["instanceid"] == instance_id
    assert started["data"]  # data=事件字段
    assert started["time"]
    with engine.session_factory() as session:
        row = session.get(EventOutboxRow, started["id"])
        assert row is not None and row.delivered_at is not None  # id＝outbox 行 id


def test_consumer_idempotent_on_redelivery(engine: ProcessEngine):
    """验收（P0-3）：至少一次交付下的重发，按 id 幂等去重。"""
    _run_flow(engine)
    inner: list[dict] = []
    sink = IdempotentSink(inner.append)
    consumer = OutboxConsumer(engine.session_factory, sink)
    total = consumer.drain()
    assert total == len(inner) > 0

    # 模拟「送达后崩溃」：送达标记回滚 → 同批事件重发
    with engine.session_factory() as session, session.begin():
        session.execute(update(EventOutboxRow).values(delivered_at=None))
    redelivered = consumer.drain()
    assert redelivered == total  # 引擎侧确实重发了同一批
    assert len(inner) == total  # 幂等消费端只算一次


def test_purge_delivered_respects_retention(engine: ProcessEngine):
    """保留口径（事件 review C1 拍板「默认永久保留」）：缺省不清理——事件表
    兼任审计历史；显式传 retention_days 才删过保行。"""
    _run_flow(engine)
    consumer = OutboxConsumer(engine.session_factory, lambda env: None)
    assert consumer.poll() > 0

    stale = utc_now() - timedelta(days=91)
    with engine.session_factory() as session, session.begin():
        session.execute(update(EventOutboxRow).values(delivered_at=stale))
    # 缺省（None）＝不清理：过保行仍在（修前默认 90 天会静默删审计）
    assert consumer.purge_delivered() == 0
    with engine.session_factory() as session:
        assert session.scalars(select(EventOutboxRow)).first() is not None
    # 显式传天数才清理
    assert consumer.purge_delivered(retention_days=90) > 0
    with engine.session_factory() as session:
        assert session.scalars(select(EventOutboxRow)).first() is None


def test_activity_level_whitelist_keeps_incidents():
    """分级白名单（事件 review C2）：activity 档保 process_/node_/incident_，
    故障告警不再被静默吞掉；task_*/message_* 只在 audit/full 档。"""
    from flowork.engine.config import EngineConfig, should_record_event

    activity = EngineConfig(history_level="activity")
    assert should_record_event(activity, "incident_raised")  # 修前该档吞故障告警
    assert should_record_event(activity, "process_started")
    assert should_record_event(activity, "node_entered")
    assert not should_record_event(activity, "task_created")
    assert not should_record_event(activity, "message_received")
    full = EngineConfig(history_level="full")
    assert should_record_event(full, "task_created")
    none_level = EngineConfig(history_level="none")
    assert not should_record_event(none_level, "incident_raised")


def test_idempotent_sink_bounded_memory():
    sink = IdempotentSink(lambda env: None, memory=2)
    for event_id in ("e1", "e2", "e3"):  # e1 被挤出记忆集
        sink({"id": event_id})
    counts: list[str] = []
    bounded = IdempotentSink(counts.append, memory=2)
    for event_id in ("e1", "e2", "e3", "e2", "e3"):
        bounded({"id": event_id})
    assert [env["id"] for env in counts] == ["e1", "e2", "e3"]  # 窗口内重发去重


def test_idempotent_sink_emit_failure_keeps_at_least_once():
    """sink 瞬时失败不丢消息（2026-10-21 内核 review P1-5 回归）。

    修前先记已见再投递：sink 抖一下 → 事务回滚 → 下轮重投同 id 被吞——
    「至少一次」变「丢消息」。现先投递后记，失败的重投会再次到达 sink。
    """
    delivered: list[dict] = []
    state = {"fail": True}

    def flaky(envelope: dict) -> None:
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("下游 500")
        delivered.append(envelope)

    sink = IdempotentSink(flaky)
    envelope = {"id": "evt-1", "type": "example.test", "data": {}}
    with pytest.raises(RuntimeError):
        sink(envelope)  # 首投失败：不得记已见
    sink(envelope)  # 重投：至少一次语义要求再次到达
    assert len(delivered) == 1
    sink(envelope)  # 成功后的同 id 重发：幂等丢弃
    assert len(delivered) == 1
