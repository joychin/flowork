"""等待节点（kernel-v2 spec §7.2 / wait-node.md §3）：消息命中推进批。

覆盖：命中推进与路由、输出映射（白名单/缺键 None/全量兜底）、缓冲反查
（消息先到）、循环重订阅、correlation 求值错=技术故障、载荷越界=技术故障、
暂停拦截与恢复补消费。超时侧（到时/竞速/取消清理）随后批。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from flowork.persistence.orm import (
    MessageBufferRow,
    MessageSubscriptionRow,
    ProcessInstanceRow,
)


def _wait_flow(**wait_fields) -> ProcessDefinition:
    return (
        FlowBuilder("wf")
        .start()
        .wait("w", **wait_fields)
        .user_task("after", assignee="'u1'")
        .end()
        .connect("start", "w")
        .connect("w", "after")
        .connect("after", "end")
        .build()
    )


def _deploy_and_start(engine: ProcessEngine, definition: ProcessDefinition) -> str:
    engine.repository.deploy(definition)
    return engine.runtime.start_process("wf", start_user="u_start")


def _open_subscriptions(engine: ProcessEngine, instance_id: str) -> list[MessageSubscriptionRow]:
    with engine.session_factory() as session:
        return list(
            session.scalars(
                select(MessageSubscriptionRow).where(
                    MessageSubscriptionRow.instance_id == instance_id,
                    MessageSubscriptionRow.state == "open",
                )
            ).all()
        )


def _buffer_count(engine: ProcessEngine) -> int:
    with engine.session_factory() as session:
        return len(session.scalars(select(MessageBufferRow)).all())


def _instance_of(engine: ProcessEngine, instance_id: str) -> ProcessInstanceRow:
    with engine.session_factory() as session:
        row = session.get(ProcessInstanceRow, instance_id)
        assert row is not None
        session.refresh(row)
        return row


# —— ① 命中推进：订阅登记 → 消息命中 → 载荷全量合并 → 推进 ————


def test_wait_matched_advances_and_merges_payload():
    engine = ProcessEngine()
    instance_id = _deploy_and_start(engine, _wait_flow(message={"name": "m1"}))
    subs = _open_subscriptions(engine, instance_id)
    assert [(s.name, s.correlation_key, s.node_id) for s in subs] == [("m1", "", "w")]

    hit = engine.messages.publish_message("m1", payload={"a": 1}, message_id="x1")
    assert hit is True
    assert engine.runtime.variables_of(instance_id) == {"a": 1}  # 全量合并兜底
    tasks = engine.task.list_tasks(instance_id=instance_id)
    assert [t.activity_code for t in tasks] == ["after"]  # 命中即推进
    assert not _open_subscriptions(engine, instance_id)  # matched 定格（非 open）
    correlated = [
        e for e in engine.history.events(instance_id) if e.event_name == "message_correlated"
    ]
    assert correlated and correlated[0].payload["node_id"] == "w"


# —— ② 输出映射：白名单写入 / 缺键 None ————


def test_wait_output_mappings_whitelist():
    engine = ProcessEngine()
    instance_id = _deploy_and_start(
        engine,
        _wait_flow(
            message={"name": "m1"},
            output_mappings={"doc": "payload.doc_id"},
        ),
    )
    engine.messages.publish_message("m1", payload={"doc_id": "D1", "extra": "x"}, message_id="x2")
    assert engine.runtime.variables_of(instance_id) == {"doc": "D1"}  # extra 不入变量
    tasks = engine.task.list_tasks(instance_id=instance_id)
    assert [t.activity_code for t in tasks] == ["after"]


def test_wait_output_mappings_missing_key_yields_none():
    engine = ProcessEngine()
    instance_id = _deploy_and_start(
        engine,
        _wait_flow(message={"name": "m1"}, output_mappings={"doc": "payload.doc_id"}),
    )
    engine.messages.publish_message("m1", payload={}, message_id="x3")
    assert engine.runtime.variables_of(instance_id) == {"doc": None}


# —— ③ 缓冲反查：消息先到、订阅后建 ————


def test_message_before_wait_buffered_then_retrohit():
    engine = ProcessEngine()
    definition = (
        FlowBuilder("wf")
        .start()
        .user_task("gate", assignee="'u_g'")
        .wait("w", message={"name": "m1"})
        .user_task("after", assignee="'u1'")
        .end()
        .connect("start", "gate")
        .connect("gate", "w")
        .connect("w", "after")
        .connect("after", "end")
        .build()
    )
    instance_id = _deploy_and_start(engine, definition)
    assert engine.messages.publish_message("m1", payload={"a": 1}) is False  # 入缓冲
    assert _buffer_count(engine) == 1

    gate = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(gate.id, actor="u_g")
    # wait 激活即反查缓冲 → 命中 → 自动推进到 after
    tasks = engine.task.list_tasks(instance_id=instance_id)
    assert [t.activity_code for t in tasks] == ["after"]
    assert _buffer_count(engine) == 0
    assert engine.runtime.variables_of(instance_id) == {"a": 1}


# —— ④ 循环重入：离开即关、再进重订（快照重新求值） ————


def test_wait_loop_resubscribes():
    engine = ProcessEngine()
    definition = (
        FlowBuilder("wf")
        .start()
        .wait("w", message={"name": "m1"}, output_mappings={"amount": "payload.amount"})
        .user_task("again", assignee="'u1'")
        .end()
        .connect("start", "w")
        .flow("w", "again", condition="amount < 1")
        .flow("w", "end", default=True)
        .connect("again", "w")
        .build()
    )
    instance_id = _deploy_and_start(engine, definition)

    engine.messages.publish_message("m1", payload={"amount": 0}, message_id="r1")
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["again"]
    assert not _open_subscriptions(engine, instance_id)  # 离开即关

    engine.task.complete(engine.task.list_tasks(instance_id=instance_id)[0].id, actor="u1")
    assert len(_open_subscriptions(engine, instance_id)) == 1  # 重入重订

    engine.messages.publish_message("m1", payload={"amount": 1}, message_id="r2")
    assert _instance_of(engine, instance_id).state == "completed"  # amount=1 走默认边到 end


# —— ⑤ 错误语义：求值错/非 string=技术故障（对齐 start_on_message） ————


def test_wait_correlation_runtime_error_is_fault():
    engine = ProcessEngine()
    engine.repository.deploy(_wait_flow(message={"name": "m1", "correlation_key": "payload.x"}))
    with pytest.raises(FloworkError):
        engine.runtime.start_process("wf", start_user="u_start")  # 激活即求值失败


def test_wait_nonstring_correlation_is_fault():
    engine = ProcessEngine()
    engine.repository.deploy(_wait_flow(message={"name": "m1", "correlation_key": "1 + 1"}))
    with pytest.raises(FloworkError):
        engine.runtime.start_process("wf", start_user="u_start")


def test_wait_payload_contract_violation_is_fault():
    engine = ProcessEngine()
    definition = _wait_flow(message={"name": "m1"})
    definition.input = {
        "type": "object",
        "properties": {"a": {"type": "number"}},
        "additionalProperties": False,
    }
    instance_id = _deploy_and_start(engine, definition)
    with pytest.raises(FloworkError):  # node_output 边界拒收 extra
        engine.messages.publish_message("m1", payload={"a": 1, "extra": "x"}, message_id="x9")
    assert _buffer_count(engine) == 0  # 消息不落库不缓冲
    assert _open_subscriptions(engine, instance_id)  # 订阅保持，等下一条合法消息


# —— ⑥ 暂停拦截与恢复补消费（spec §7.2 暂停=全动作拦截） ————


def test_pause_blocks_consume_and_resume_polls_buffer():
    engine = ProcessEngine()
    instance_id = _deploy_and_start(engine, _wait_flow(message={"name": "m1"}))
    engine.runtime.pause_process(instance_id, actor="u_ops")
    assert engine.messages.publish_message("m1", payload={"a": 1}) is False  # 拦截→落缓冲
    assert _open_subscriptions(engine, instance_id)  # 订阅保持（spec §7 原文）
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == []

    engine.runtime.resume_process(instance_id, actor="u_ops")
    # 恢复即补消费：缓冲反查 → 命中 → 推进
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["after"]
    assert _buffer_count(engine) == 0
    assert engine.runtime.variables_of(instance_id) == {"a": 1}


# —— ⑦ 到时侧：纯延时 / 竞速 / 暂停冻结（spec §7.2 三模式） ————


class _Clock:
    def __init__(self) -> None:
        from datetime import datetime

        self.now = datetime(2026, 10, 9, 9, 0, 0)

    def __call__(self):
        return self.now

    def advance(self, minutes: int) -> None:
        from datetime import timedelta

        self.now += timedelta(minutes=minutes)


def _clock_engine() -> tuple[ProcessEngine, _Clock]:
    from flowork.engine.config import EngineConfig

    clock = _Clock()
    return ProcessEngine(EngineConfig(now=clock)), clock


def test_pure_delay_advances_on_time():
    engine, clock = _clock_engine()
    instance_id = _deploy_and_start(engine, _wait_flow(timeout={"after_minutes": 5}))
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == []

    clock.advance(4)
    engine.jobs.tick()
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == []

    clock.advance(2)
    engine.jobs.tick()
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["after"]
    events = [e.event_name for e in engine.history.events(instance_id)]
    assert "timer_fired" in events  # spec §7 承诺事件首发


def test_race_timeout_first_routes_on_elapsed():
    engine, clock = _clock_engine()
    definition = (
        FlowBuilder("wf")
        .start()
        .wait("w", message={"name": "m1"}, timeout={"after_minutes": 5})
        .user_task("late", assignee="'u_late'")
        .user_task("ok", assignee="'u_ok'")
        .end()
        .connect("start", "w")
        .flow("w", "late", condition="wait.result == 'elapsed'")
        .flow("w", "ok", condition="wait.result == 'matched'")
        .connect("late", "end")
        .connect("ok", "end")
        .build()
    )
    instance_id = _deploy_and_start(engine, definition)
    clock.advance(6)
    engine.jobs.tick()
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["late"]
    # 迟到的消息不复活：订阅已 elapsed，publish 落缓冲返回 False
    assert engine.messages.publish_message("m1", payload={"a": 1}) is False


def test_race_message_first_then_timer_skips():
    engine, clock = _clock_engine()
    instance_id = _deploy_and_start(
        engine, _wait_flow(message={"name": "m1"}, timeout={"after_minutes": 5})
    )
    engine.messages.publish_message("m1", payload={"a": 1}, message_id="w1")
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["after"]

    clock.advance(6)
    assert engine.jobs.tick() == 0  # 到时 Job 已随命中清理，无 Job 可执行


def test_pause_freezes_wait_timer():
    engine, clock = _clock_engine()
    instance_id = _deploy_and_start(engine, _wait_flow(timeout={"after_minutes": 5}))
    engine.runtime.pause_process(instance_id, actor="u_ops")
    clock.advance(10)
    engine.jobs.tick()
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == []

    engine.runtime.resume_process(instance_id, actor="u_ops")  # run_at 顺延暂停时长
    engine.jobs.tick()  # 尚未到期（顺延后）
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == []
    clock.advance(10)
    engine.jobs.tick()
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["after"]


def test_terminate_closes_subscription_and_job():
    engine, _clock = _clock_engine()
    instance_id = _deploy_and_start(
        engine, _wait_flow(message={"name": "m1"}, timeout={"after_minutes": 5})
    )
    engine.runtime.terminate_process(instance_id, actor="u_ops")
    assert _open_subscriptions(engine, instance_id) == []
    with engine.session_factory() as session:
        from flowork.persistence.orm import JobRow

        jobs = list(
            session.scalars(
                select(JobRow).where(JobRow.instance_id == instance_id, JobRow.state == "scheduled")
            ).all()
        )
        assert jobs == []  # 到时 Job 随 on_cancel 清理


def test_any_join_race_closes_loser_subscription():
    engine, _clock = _clock_engine()
    definition = (
        FlowBuilder("wf")
        .start()
        .wait("wa", message={"name": "ma"})
        .wait("wb", message={"name": "mb"})
        .user_task("merge", assignee="'u1'", advance_policy="any")
        .end()
        .connect("start", "wa")
        .connect("start", "wb")
        .connect("wa", "merge")
        .connect("wb", "merge")
        .connect("merge", "end")
        .build()
    )
    instance_id = _deploy_and_start(engine, definition)
    assert len(_open_subscriptions(engine, instance_id)) == 2  # 双等待并行

    engine.messages.publish_message("mb", payload={"winner": "b"}, message_id="wb1")
    # 先到者赢：merge(any) 放行 → 竞速收尾取消败者 wa → 其订阅关闭
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["merge"]
    assert _open_subscriptions(engine, instance_id) == []
    assert (
        engine.messages.publish_message("ma", payload={}, message_id="wa1") is False
    )  # 败者订阅已关


# —— ⑧ 配套面：correlate_message / waiting / 路由审计快照（spec §7.2 §5） ————


def test_correlate_hit_returns_receipt_and_advances():
    engine = ProcessEngine()
    engine.repository.deploy(_wait_flow(message={"name": "m1"}))
    instance_id = engine.runtime.start_process("wf", start_user="u_start", business_key="BK-7")

    result = engine.messages.correlate_message("m1", payload={"a": 1}, message_id="c1")
    assert result == {
        "matched": True,
        "duplicate": False,
        "instance_id": instance_id,
        "node_id": "w",
        "message_name": "m1",
        "business_key": "BK-7",
        "definition_code": "wf",
    }
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["after"]


def test_correlate_no_subscriber_returns_none_without_buffering():
    engine = ProcessEngine()
    engine.repository.deploy(_wait_flow(message={"name": "m1"}))
    assert engine.messages.correlate_message("m1", payload={"a": 1}) is None
    assert _buffer_count(engine) == 0  # 不缓冲：消息不留存


def test_correlate_duplicate_replays_first_receipt():
    engine = ProcessEngine()
    engine.repository.deploy(_wait_flow(message={"name": "m1"}))
    instance_id = engine.runtime.start_process("wf", start_user="u_start")
    first = engine.messages.correlate_message("m1", payload={"a": 1}, message_id="dup")
    assert first is not None and first["duplicate"] is False

    second = engine.messages.correlate_message("m1", payload={"a": 2}, message_id="dup")
    assert second is not None and second["duplicate"] is True  # 幂等回放
    assert second["instance_id"] == first["instance_id"]
    assert engine.runtime.variables_of(instance_id) == {"a": 1}  # 不二次消费


def test_correlate_paused_instance_returns_none():
    engine = ProcessEngine()
    engine.repository.deploy(_wait_flow(message={"name": "m1"}))
    instance_id = engine.runtime.start_process("wf", start_user="u_start")
    engine.runtime.pause_process(instance_id, actor="u_ops")
    assert engine.messages.correlate_message("m1", payload={"a": 1}) is None
    assert _buffer_count(engine) == 0  # 暂停拦截且不留存


def test_waiting_lists_open_subscriptions():
    engine = ProcessEngine()
    engine.repository.deploy(_wait_flow(message={"name": "m1"}))
    instance_id = engine.runtime.start_process("wf", start_user="u_start", business_key="BK-9")
    rows = engine.messages.waiting(name="m1")
    assert len(rows) == 1
    assert rows[0]["instance_id"] == instance_id
    assert rows[0]["node_id"] == "w"
    assert rows[0]["business_key"] == "BK-9"
    assert rows[0]["definition_code"] == "wf"
    engine.messages.correlate_message("m1", payload={}, message_id="w9")
    assert engine.messages.waiting() == []  # 消费后不在等待列表


def test_route_audit_snapshots_wait_result():
    engine, clock = _clock_engine()
    definition = (
        FlowBuilder("wf")
        .start()
        .wait("w", message={"name": "m1"}, timeout={"after_minutes": 5})
        .user_task("late", assignee="'u_late'")
        .end()
        .connect("start", "w")
        .flow("w", "late", condition="wait.result == 'elapsed'")
        .connect("w", "end")
        .connect("late", "end")
        .build()
    )
    instance_id = _deploy_and_start(engine, definition)
    clock.advance(6)
    engine.jobs.tick()
    decisions = engine.history.route_decisions(instance_id)
    wait_decision = next(d for d in decisions if d.source_activity == "w")
    assert wait_decision.snapshot["wait"] == {
        "result": "elapsed",
        "message_name": None,
        "message_id": None,
    }
