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
