"""事件全序与时钟统一（2026-10-13 事件 review A1/A2）。

来源：事件 review 实证——同命令多事件 occurred_at 微秒平局，poll 按
(occurred_at, id) 排序而 id 是随机 UUID，start 命令排出「node_exited 先于
node_entered」（start 节点先退后进）；且 occurred_at 取列默认墙钟，与
config.now 的任务/令牌时间戳不同源。修复＝可排序事件 id（时间前缀+nonce+
计数）+ 命令级统一 stamp。验收：同命令/跨命令（冻结时钟）序列钉死、
occurred_at 跟随引擎钟、消费器投递序与查询序一致。
"""

from __future__ import annotations

from datetime import datetime

from flowork.api import FlowBuilder, IdempotentSink, OutboxConsumer, ProcessEngine
from flowork.engine.config import EngineConfig


class FakeClock:
    """冻结钟：恒返回同一时刻（平局判例的判别力所在）。"""

    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, 0, 0, 0)

    def __call__(self) -> datetime:
        return self.value


def _two_step_engine() -> ProcessEngine:
    definition = (
        FlowBuilder("evt_order")
        .start()
        .user_task("a", assignee="'u1'")
        .user_task("b", assignee="'u2'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    engine = ProcessEngine()
    engine.repository.deploy(definition)
    return engine


def _names(engine: ProcessEngine, instance_id: str) -> list[str]:
    return [e.event_name for e in engine.history.events(instance_id, limit=100)]


def test_start_command_sequence_deterministic():
    """单命令五事件钉死精确序（修前随机平局可产出 node_exited 在 node_entered 前）。"""
    engine = _two_step_engine()
    instance_id = engine.runtime.start_process("evt_order", start_user="u_s")
    assert _names(engine, instance_id) == [
        "process_started",
        "node_entered",
        "node_exited",
        "node_entered",
        "task_created",
    ]
    engine.close()


def test_complete_command_sequence_deterministic():
    engine = _two_step_engine()
    instance_id = engine.runtime.start_process("evt_order", start_user="u_s")
    engine.task.complete(engine.task.list_tasks(instance_id=instance_id)[0].id, actor="u1")
    assert _names(engine, instance_id)[5:] == [
        "task_completed",
        "node_exited",
        "node_entered",
        "task_created",
    ]
    engine.close()


def test_frozen_clock_same_timestamp_full_order():
    """冻结时钟：全部事件同 occurred_at，序列仍按命令与发射顺序全序（counter 兜底）。"""
    clock = FakeClock()
    engine = ProcessEngine(EngineConfig(now=clock))
    definition = (
        FlowBuilder("evt_frozen")
        .start()
        .user_task("a", assignee="'u1'")
        .user_task("b", assignee="'u2'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("evt_frozen", start_user="u_s")
    engine.task.complete(engine.task.list_tasks(instance_id=instance_id)[0].id, actor="u1")
    events = engine.history.events(instance_id, limit=100)
    # A2：occurred_at 全部等于引擎钟（列默认墙钟是 2026-10，必不等）
    assert all(e.occurred_at == clock.value for e in events)
    assert [e.event_name for e in events] == [
        "process_started",
        "node_entered",
        "node_exited",
        "node_entered",
        "task_created",
        "task_completed",
        "node_exited",
        "node_entered",
        "task_created",
    ]
    engine.close()


def test_consumer_delivery_order_matches_history():
    """消费器投递序（drain）与实例查询序逐条一致——订阅方与审计面看到同一序列。"""
    engine = _two_step_engine()
    instance_id = engine.runtime.start_process("evt_order", start_user="u_s")
    engine.task.complete(engine.task.list_tasks(instance_id=instance_id)[0].id, actor="u1")
    delivered: list[dict] = []
    consumer = OutboxConsumer(engine.session_factory, IdempotentSink(delivered.append))
    consumer.drain()  # 含 definition_deployed 等全局事件，按实例过滤后比较
    consumed_names = [env["type"] for env in delivered if env.get("instanceid") == instance_id]
    assert consumed_names == _names(engine, instance_id)
    assert len(consumed_names) == 9
    engine.close()
