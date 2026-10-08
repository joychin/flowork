"""逐节点步进断言器：每推进一次校验"活跃节点集 + 待办（节点×办理人）+ 实例状态"。

只读公共查询面（history.tokens / task.list_tasks / runtime.get_instance）；
失败信息携带期望 vs 实际，直接指出卡在哪个节点。
"""

from __future__ import annotations

from typing import Any

from flowork.engine.engine import ProcessEngine


def active_nodes(engine: ProcessEngine, instance_id: str) -> set[str]:
    """当前活跃令牌所在节点（未完成令牌）。"""
    return {
        token.activity_code
        for token in engine.history.tokens(instance_id)
        if token.state == "unfinished"
    }


def ready_pairs(engine: ProcessEngine, instance_id: str) -> set[tuple[str, str]]:
    """当前待办 (activity, assignee) 集合；候选任务 assignee 为空串。"""
    return {
        (task.activity_code, task.assignee)
        for task in engine.task.list_tasks(instance_id=instance_id)
    }


def expect_position(
    engine: ProcessEngine,
    instance_id: str,
    *,
    nodes: set[str] | None = None,
    tasks: set[tuple[str, str]] | None = None,
    state: str | None = None,
    note: str = "",
) -> None:
    """断言流程当前位置（三方任选；失败时输出期望与实际对照）。"""
    actual_nodes = active_nodes(engine, instance_id)
    actual_tasks = ready_pairs(engine, instance_id)
    actual_state = engine.runtime.get_instance(instance_id).state
    problems: list[str] = []
    if nodes is not None and actual_nodes != set(nodes):
        problems.append(f"活跃节点 期望={sorted(nodes)} 实际={sorted(actual_nodes)}")
    if tasks is not None and actual_tasks != set(tasks):
        problems.append(f"待办 期望={sorted(tasks)} 实际={sorted(actual_tasks)}")
    if state is not None and actual_state != state:
        problems.append(f"实例状态 期望={state} 实际={actual_state}")
    if problems:
        raise AssertionError(f"[{note or instance_id}] 逐节点位置断言失败：" + "；".join(problems))


def task_of(engine: ProcessEngine, instance_id: str, assignee: str) -> str:
    """取指定办理人当前待办 ID（无待办即断言失败）。"""
    tasks = engine.task.list_tasks(instance_id=instance_id, assignee=assignee)
    assert tasks, f"{assignee} 在实例 {instance_id} 无待办"
    return tasks[0].id


def complete_of(engine: ProcessEngine, instance_id: str, assignee: str, **kwargs: Any) -> Any:
    """办理指定人当前待办并返回结果。"""
    return engine.task.complete(task_of(engine, instance_id, assignee), actor=assignee, **kwargs)


def reject_of(engine: ProcessEngine, instance_id: str, assignee: str, **kwargs: Any) -> Any:
    """驳回指定人当前待办并返回结果。"""
    return engine.task.reject(task_of(engine, instance_id, assignee), actor=assignee, **kwargs)


def entered_activities(engine: ProcessEngine, instance_id: str) -> list[str]:
    """已进入过的节点序列（node_entered 事件序），用于断言自动节点被走通。"""
    return [
        event.payload["activity_code"]
        for event in engine.history.events(instance_id)
        if event.event_name == "node_entered" and "activity_code" in event.payload
    ]
