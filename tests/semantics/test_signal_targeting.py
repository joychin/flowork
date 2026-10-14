"""signal 实例定向防串单（全仓 review 2026-10-13 批 1 P0-2）。

修前：correlate_message 全局 FCFS——两实例等同一 (name, key) 时对 B 发
signal，消息被先到的 A 消费并推进 A，回执核验只能拒绝响应、挡不住消费
（「报错的同时产生了不可撤销的错误副作用」）。修后 correlate 支持
instance_id 定向：signal 面只消费本实例的订阅。
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine


def _two_waiters() -> tuple[ProcessEngine, str, str]:
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("sig")
        .start()
        .wait("w", message={"name": "go"})
        .user_task("after", assignee="'u1'")
        .end()
        .connect("start", "w")
        .connect("w", "after")
        .connect("after", "end")
        .build()
    )
    a = engine.runtime.start_process("sig", start_user="u1")
    b = engine.runtime.start_process("sig", start_user="u1")
    assert engine.runtime.get_instance(a).state == "running"
    assert engine.runtime.get_instance(b).state == "running"
    return engine, a, b


def test_correlate_directed_consumes_only_target_instance():
    engine, a, b = _two_waiters()
    receipt = engine.messages.correlate_message("go", instance_id=b)
    assert receipt is not None and receipt["instance_id"] == b
    assert engine.runtime.get_instance(b).state == "running"  # b 已被唤醒推进到 after
    assert engine.runtime.get_instance(a).state == "running"  # a 未被消费（仍在 wait）
    tasks_a = engine.task.list_tasks(instance_id=a)
    assert tasks_a == []  # a 的 wait 未收口，无 after 待办
    assert engine.task.list_tasks(instance_id=b)[0].activity_code == "after"


def test_correlate_directed_missing_subscription_returns_none():
    engine, a, _ = _two_waiters()
    assert engine.messages.correlate_message("go", instance_id="ghost") is None
    # 两人均未被消费
    assert engine.runtime.get_instance(a).state == "running"


def test_correlate_directed_then_global_still_reaches_first_waiter():
    """定向消费 b 后，全局 correlate（publish 语义）仍按 FCFS 到 a——
    定向只约束 signal 面，不改变 publish 的匹配顺序。"""
    engine, a, b = _two_waiters()
    engine.messages.correlate_message("go", instance_id=b)
    receipt = engine.messages.correlate_message("go")
    assert receipt is not None and receipt["instance_id"] == a
