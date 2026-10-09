"""start_on_message（kernel-v2 spec §7.1）：消息启动实例。

验收六类：直接启动（映射/发起人/业务键/事件归因）、不回溯缓冲、message_id
幂等、correlation 防串单、在途节点订阅优先、retire 失效；附 input 契约拒收。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, StartMessageDecl
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from flowork.persistence.orm import MessageBufferRow, ProcessInstanceRow


def _flow(code: str = "starter") -> ProcessDefinition:
    return (
        FlowBuilder(code)
        .start()
        .user_task("work", assignee="'u_worker'")
        .end()
        .connect("start", "work")
        .connect("work", "end")
        .build()
    )


def _deploy(engine: ProcessEngine, definition: ProcessDefinition, decl: StartMessageDecl) -> None:
    definition.start_on_message = [decl]
    engine.repository.deploy(definition)


def _instances(engine: ProcessEngine) -> list[ProcessInstanceRow]:
    with engine.session_factory() as session:
        return list(session.scalars(select(ProcessInstanceRow)).all())


def _buffered(engine: ProcessEngine) -> int:
    with engine.session_factory() as session:
        return len(session.scalars(select(MessageBufferRow)).all())


# —— ① 直接启动：映射/发起人缺省合成/业务键/事件归因 ————


def test_message_starts_instance_with_mappings():
    engine = ProcessEngine()
    _deploy(
        engine,
        _flow(),
        StartMessageDecl(
            name="bid_received",
            input_mappings={"amount": "payload.amount", "project": "payload.project_no"},
            business_key="payload.project_no",
        ),
    )
    hit = engine.messages.publish_message(
        "bid_received", "BK-9", {"amount": 500, "project_no": "BK-9"}, message_id="m1"
    )
    assert hit is True
    rows = _instances(engine)
    assert len(rows) == 1
    inst = rows[0]
    assert inst.start_user == "message:bid_received"  # 发起人缺省合成（spec §7.1）
    assert inst.business_key == "BK-9"
    assert engine.runtime.variables_of(inst.id) == {"amount": 500, "project": "BK-9"}
    tasks = engine.task.list_tasks(instance_id=inst.id)
    assert [t.assignee for t in tasks] == ["u_worker"]  # 实例正常推进到首个人工节点

    started = next(e for e in engine.history.events(inst.id) if e.event_name == "process_started")
    assert started.payload["start_message"] == {"name": "bid_received", "message_id": "m1"}
    correlated = [e for e in engine.history.events(inst.id) if e.event_name == "message_correlated"]
    assert correlated and correlated[0].payload["node_id"] == "(start)"


def test_start_user_expression_overrides_default():
    engine = ProcessEngine()
    _deploy(engine, _flow(), StartMessageDecl(name="hook", start_user="payload.operator"))
    engine.messages.publish_message("hook", "", {"operator": "u_bot"})
    rows = _instances(engine)
    assert len(rows) == 1 and rows[0].start_user == "u_bot"


# —— ② 不回溯缓冲：部署时刻起算 ————


def test_deploy_does_not_replay_buffer():
    engine = ProcessEngine()
    assert engine.messages.publish_message("late", "", {"x": 1}) is False  # 无 starter → 缓冲
    _deploy(engine, _flow(), StartMessageDecl(name="late"))
    assert _instances(engine) == []  # 不回溯：部署不消费既有缓冲（spec §7.1）
    assert _buffered(engine) == 1

    assert engine.messages.publish_message("late", "", {"x": 2}) is True
    assert len(_instances(engine)) == 1
    assert _buffered(engine) == 1  # 首条仍在缓冲（留给在途节点订阅）


# —— ③ message_id 幂等：同 id 至多启动一个 ————


def test_same_message_id_starts_at_most_once():
    engine = ProcessEngine()
    _deploy(engine, _flow(), StartMessageDecl(name="once"))
    assert engine.messages.publish_message("once", "", {"a": 1}, message_id="dup") is True
    assert (
        engine.messages.publish_message("once", "", {"a": 1}, message_id="dup") is False
    )  # 防重定格（matched 订阅行进并集）静默忽略
    assert len(_instances(engine)) == 1


# —— ④ correlation 防串单 ————


def test_correlation_mismatch_buffered_not_started():
    engine = ProcessEngine()
    _deploy(
        engine,
        _flow(),
        StartMessageDecl(name="paid", correlation_key="payload.order_no"),
    )
    # 载荷求值键 == 发布携带键 → 命中启动
    assert engine.messages.publish_message("paid", "ORD-1", {"order_no": "ORD-1"}) is True
    # 求值键 ≠ 携带键 → 防串单不启动，消息入缓冲
    assert engine.messages.publish_message("paid", "ORD-2", {"order_no": "OTHER"}) is False
    assert len(_instances(engine)) == 1
    assert _buffered(engine) == 1


def test_no_correlation_decl_starts_on_any_key():
    engine = ProcessEngine()
    _deploy(engine, _flow(), StartMessageDecl(name="any"))
    assert engine.messages.publish_message("any", "WHATEVER", {}) is True


# —— ⑤ 在途节点订阅优先 ————


def test_node_subscription_takes_priority_over_start():
    engine = ProcessEngine()
    waiting = _flow("waiting")
    engine.repository.deploy(waiting)
    instance_id = engine.runtime.start_process("waiting", start_user="u_s")
    token = next(t for t in engine.history.tokens(instance_id) if t.state == "unfinished")
    engine.messages.subscribe(instance_id, token.id, token.activity_code, "paid", "ORD-9")

    _deploy(engine, _flow(), StartMessageDecl(name="paid"))  # 同名 starter 后部署

    assert engine.messages.publish_message("paid", "ORD-9", {"ok": True}) is True
    assert len(_instances(engine)) == 1  # 喂了在等实例，未启动新实例
    assert _buffered(engine) == 0


# —— ⑥ retire 即失效（定义派生态） ————


def test_retired_definition_no_longer_starts():
    engine = ProcessEngine()
    _deploy(engine, _flow(), StartMessageDecl(name="gone"))
    engine.repository.retire("starter", actor="t")
    assert engine.messages.publish_message("gone", "", {}) is False
    assert _instances(engine) == []


# —— 附：input 契约 start 边界拒收（技术故障，publish 整体失败） ————


def test_input_contract_violation_fails_publish():
    engine = ProcessEngine()
    definition = _flow()
    definition.input = {
        "type": "object",
        "required": ["amount"],
        "properties": {"amount": {"type": "number"}},
    }
    _deploy(engine, definition, StartMessageDecl(name="strict"))
    with pytest.raises(FloworkError):
        engine.messages.publish_message("strict", "", {"nothing": 1})
    assert _instances(engine) == []  # 同事务回滚：无实例
    assert _buffered(engine) == 0  # 消息不落库不缓冲（spec §7.1 错误语义）
