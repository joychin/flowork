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
        .user_task("a", assignee="u_a")
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
    _run_flow(engine)
    consumer = OutboxConsumer(engine.session_factory, lambda env: None)
    assert consumer.poll() > 0
    assert consumer.purge_delivered() == 0  # 刚送达，未过保留期

    stale = utc_now() - timedelta(days=91)
    with engine.session_factory() as session, session.begin():
        session.execute(update(EventOutboxRow).values(delivered_at=stale))
    assert consumer.purge_delivered() > 0
    with engine.session_factory() as session:
        assert session.scalars(select(EventOutboxRow)).first() is None


def test_idempotent_sink_bounded_memory():
    sink = IdempotentSink(lambda env: None, memory=2)
    for event_id in ("e1", "e2", "e3"):  # e1 被挤出记忆集
        sink({"id": event_id})
    counts: list[str] = []
    bounded = IdempotentSink(counts.append, memory=2)
    for event_id in ("e1", "e2", "e3", "e2", "e3"):
        bounded({"id": event_id})
    assert [env["id"] for env in counts] == ["e1", "e2", "e3"]  # 窗口内重发去重
