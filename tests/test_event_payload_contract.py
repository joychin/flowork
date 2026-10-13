"""事件载荷契约（2026-10-13 事件 review B1 起步；批3 扩全字段）。

来源：task 族事件此前只有 task_id/assignee/actor——消费者定位节点须反查
wf_task_history，且转办克隆的 task_created 与主路径载荷形状不一致。
本契约钉死：经 _emit_task_event 咽喉的事件与 task_withdrawn 必带
activity_code（+task_type），防形状回退。
"""

from __future__ import annotations

from flowork.api import FlowBuilder, ProcessEngine


def _engine_with_flow() -> ProcessEngine:
    definition = (
        FlowBuilder("evt_payload")
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


def _events_of(engine: ProcessEngine, instance_id: str, name: str) -> list[dict]:
    return [
        e.payload for e in engine.history.events(instance_id, limit=100) if e.event_name == name
    ]


def test_transfer_events_carry_activity_code_and_task_type():
    """转办双事件（transferred + 克隆 created）同形：activity_code/task_type 齐备。"""
    engine = _engine_with_flow()
    instance_id = engine.runtime.start_process("evt_payload", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.transfer(task.id, to_user="u9", actor="u1")
    transferred = _events_of(engine, instance_id, "task_transferred")
    assert transferred and transferred[0]["activity_code"] == "a"
    assert transferred[0]["task_type"] == "approval"
    created = _events_of(engine, instance_id, "task_created")
    # 克隆任务的 task_created 与主路径同形（修前缺 activity_code/task_type）
    assert created[-1]["activity_code"] == "a"
    assert created[-1]["task_type"] == "approval"
    engine.close()


def test_withdrawn_event_carries_activity_code():
    """task_withdrawn 两分支（撤票/重建）都带 activity_code——上轮问答实证的缺口。"""
    engine = _engine_with_flow()
    instance_id = engine.runtime.start_process("evt_payload", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u1", comment="ok")
    engine.task.withdraw(task.id, actor="u1", comment="撤回重办")
    withdrawn = _events_of(engine, instance_id, "task_withdrawn")
    assert len(withdrawn) == 1
    assert withdrawn[0]["activity_code"] == "a"
    assert withdrawn[0]["new_token"]  # 单任务节点走重建分支
    engine.close()
