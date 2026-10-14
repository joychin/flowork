"""错误归因帧补口（全仓 review 2026-10-13 批 3 P1-2/P2-1）。

wait 收口的路由调用与驳回路径的声明式 on_exit 原在归因 try 块外——出边
条件/钩子运行期失败时 activity_code=None，incident 定位、process_suspended
定位、on_fail 钩子三个修复面同时失效（一个漏点打穿三面）。修后两处入帧；
attribute_node_error 只补不覆盖，内层更精确帧不受影响。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError


def _incident_activity(engine: ProcessEngine, instance_id: str) -> str | None:
    raised = [
        e for e in engine.history.events(instance_id) if e.event_name == "incident_raised"
    ]
    assert raised, "应有 incident_raised"
    return raised[0].payload.get("activity_code")


def test_wait_route_error_attributes_node_and_fires_on_fail():
    """wait 出边条件运行期错：incident/挂起定位到 wait 节点，on_fail 执行。

    修前三断言全空——route 在归因帧外，activity_code=None 令 on_fail 静默
    跳过（fail_hooks 按定位找失败现场）。"""
    engine = ProcessEngine()
    on_fail_calls: list[str] = []

    def on_fail(ctx, params):
        on_fail_calls.append(ctx.node.code)

    engine.register_handler("on_fail", on_fail)
    engine.repository.deploy(
        FlowBuilder("af_wait")
        .start()
        .wait("w1", message={"name": "go"}, hooks={"on_fail": {"handler": "on_fail"}})
        .end()
        .connect("start", "w1")
        .flow("w1", "end", condition="1/0 > 0")  # 设计期不执行公式，运行期才炸
        .build()
    )
    instance_id = engine.runtime.start_process("af_wait", start_user="u1")
    assert engine.runtime.get_instance(instance_id).state == "running"

    with pytest.raises(FloworkError):
        engine.messages.publish_message("go", payload={})

    assert engine.runtime.get_instance(instance_id).state == "suspended"
    assert _incident_activity(engine, instance_id) == "w1"
    suspended = [
        e for e in engine.history.events(instance_id) if e.event_name == "process_suspended"
    ]
    assert suspended and suspended[0].payload.get("activity_code") == "w1"
    assert on_fail_calls == ["w1"]


def test_reject_declared_exit_error_attributes_node():
    """驳回路径声明式 on_exit 失败：incident 定位到本节点（修前 None）。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("af_reject")
        .start()
        .user_task("a", assignee="'u1'", hooks={"on_exit": {"handler": "missing_hook"}})
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("af_reject", start_user="u1")
    task = engine.task.list_tasks(instance_id=instance_id)[0]

    with pytest.raises(FloworkError):
        engine.task.reject(task.id, actor="u1", comment="退回")

    assert engine.runtime.get_instance(instance_id).state == "suspended"
    assert _incident_activity(engine, instance_id) == "a"
