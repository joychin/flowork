"""故障单与实例状态的交叉守卫（全仓 review 2026-10-13 批 3 P0-1）。

修前：terminate/cancel 不联动关闭 open 故障单，resolve(resume/retry) 可把
已终止实例复活为 running（终态三件套事件契约被破坏，复活后零未完令牌
永久卡死）；paused 实例上的 resume 会绕过 pause 恢复路径直翻 running。
修后三道闸：
- _resume_from_suspension 仅接受 suspended（终态拒绝、paused 指路）；
- terminate/cancel 级联时整树联动关闭 open 故障单（resolution=instance_*）；
- skip 对终态实例拒绝。
"""

from __future__ import annotations

import uuid

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError


def _engine_with_failing_action() -> ProcessEngine:
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("guard_flow")
        .start()
        .user_task("a", assignee="'u_a'")
        .service_task("boom", "会炸的步骤", action="explode", params={})
        .user_task("after", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "boom")
        .connect("boom", "after")
        .connect("after", "end")
        .build()
    )

    def explode(ctx, params):
        raise RuntimeError("外部接口炸了")

    engine.register_handler("explode", explode)
    return engine


def _bring_to_boom(engine: ProcessEngine) -> str:
    instance_id = engine.runtime.start_process("guard_flow", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    with pytest.raises(FloworkError):
        engine.task.complete(task.id, actor="u_a")  # boom 炸 → Incident + suspended
    assert engine.runtime.get_instance(instance_id).state == "suspended"
    return instance_id


def _open_incident_id(engine: ProcessEngine, instance_id: str) -> str:
    incidents = engine.runtime.list_incidents(instance_id=instance_id)
    assert incidents and incidents[0]["state"] == "open"
    return incidents[0]["id"]


def _insert_open_incident(engine: ProcessEngine, instance_id: str) -> str:
    """直插 open 故障单（构造守卫的不可达态：终态/暂停实例挂 open 单——
    正常流已被联动关单堵死，守卫防的是竞态窗与存量脏数据）。"""

    def _cmd(ctx):
        from flowork.persistence.orm import IncidentRow

        incident_id = uuid.uuid4().hex
        ctx.session.add(
            IncidentRow(
                id=incident_id,
                instance_id=instance_id,
                message="人工构造",
                error_class="RuntimeError",
            )
        )
        return incident_id

    result: str = engine.execute(_cmd)
    return result


def _event_names(engine: ProcessEngine, instance_id: str) -> list[str]:
    return [e.event_name for e in engine.history.events(instance_id)]


def test_terminate_cascades_close_open_incidents():
    """terminate 联动关闭整树 open 故障单——修后 resolve 无单可复活的入口。"""
    engine = _engine_with_failing_action()
    instance_id = _bring_to_boom(engine)
    _open_incident_id(engine, instance_id)

    engine.runtime.terminate_process(instance_id, actor="admin", reason="弃单")
    assert engine.runtime.get_instance(instance_id).state == "terminated"

    incidents = engine.runtime.list_incidents(instance_id=instance_id)
    assert incidents[0]["state"] == "resolved"
    assert incidents[0]["resolution"] == "instance_terminated"
    assert "incident_resolved" in _event_names(engine, instance_id)


def test_cancel_cascades_close_open_incidents():
    engine = _engine_with_failing_action()
    instance_id = _bring_to_boom(engine)

    engine.runtime.cancel_process(instance_id, actor="admin", reason="取消")
    incidents = engine.runtime.list_incidents(instance_id=instance_id)
    assert incidents[0]["state"] == "resolved"
    assert incidents[0]["resolution"] == "instance_cancelled"


def test_resolve_resume_on_terminated_no_longer_revives():
    """P0 主判例：挂起 → terminate → 对残存 open 单 resolve(resume)。

    联动关单后入口已被堵死（报「Incident 不存在或已处理」）；即便竞态窗
    内残存 open 单，resume 守卫也拒绝（下一条判例）——实例绝不复活。"""
    engine = _engine_with_failing_action()
    instance_id = _bring_to_boom(engine)
    incident_id = _open_incident_id(engine, instance_id)
    engine.runtime.terminate_process(instance_id, actor="admin")

    with pytest.raises(FloworkError):
        engine.runtime.resolve_incident(incident_id, action="resume", actor="ops")
    assert engine.runtime.get_instance(instance_id).state == "terminated"
    assert "process_resumed" not in _event_names(engine, instance_id)


@pytest.mark.parametrize("state_setup", ["terminated", "paused"])
def test_resume_guard_refuses_non_suspended(state_setup: str):
    """resume 守卫（防御直测）：终态拒绝复活；paused 指路 resume_process。"""
    engine = _engine_with_failing_action()
    if state_setup == "terminated":
        instance_id = _bring_to_boom(engine)
        engine.runtime.terminate_process(instance_id, actor="admin")
    else:
        # paused+open 单的构造不走故障：挂起载具此刻是 suspended，pause 只收
        # running——直接启动即暂停，open 单直插（守卫只看两态组合）
        instance_id = engine.runtime.start_process("guard_flow", start_user="u_s")
        engine.runtime.pause_process(instance_id, actor="admin", reason="盘点")
    incident_id = _insert_open_incident(engine, instance_id)

    with pytest.raises(FloworkError, match=state_setup):
        engine.runtime.resolve_incident(incident_id, action="resume", actor="ops")
    assert engine.runtime.get_instance(instance_id).state == state_setup
    assert "process_resumed" not in _event_names(engine, instance_id)


def test_retry_guard_refuses_terminated():
    engine = _engine_with_failing_action()
    instance_id = _bring_to_boom(engine)
    engine.runtime.terminate_process(instance_id, actor="admin")
    incident_id = _insert_open_incident(engine, instance_id)

    with pytest.raises(FloworkError):
        engine.runtime.resolve_incident(incident_id, action="retry", actor="ops")
    assert engine.runtime.get_instance(instance_id).state == "terminated"


def test_skip_refuses_terminated():
    engine = _engine_with_failing_action()
    instance_id = _bring_to_boom(engine)
    engine.runtime.terminate_process(instance_id, actor="admin")
    incident_id = _insert_open_incident(engine, instance_id)

    with pytest.raises(FloworkError):
        engine.runtime.resolve_incident(incident_id, action="skip", actor="ops")
    assert engine.runtime.get_instance(instance_id).state == "terminated"


def test_child_incident_closed_when_parent_terminated():
    """子实例挂 open 故障单、父实例终止——级联把子实例的故障单一并关闭。

    载具注意：子流程首节点就炸会触发「start 阶段整笔回滚」（父实例根本
    不落库），故让子先办一段用户任务再炸——父停在 call 节点等待。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("guard_child")
        .start()
        .user_task("prepare", assignee="'u_c'")
        .service_task("boom", "会炸的步骤", action="explode", params={})
        .end()
        .connect("start", "prepare")
        .connect("prepare", "boom")
        .connect("boom", "end")
        .build()
    )
    engine.repository.deploy(
        FlowBuilder("guard_parent")
        .start()
        .call_activity("call_child", "call", process_code="guard_child")
        .end()
        .connect("start", "call_child")
        .connect("call_child", "end")
        .build()
    )

    def explode(ctx, params):
        raise RuntimeError("子炸了")

    engine.register_handler("explode", explode)

    parent_id = engine.runtime.start_process("guard_parent", start_user="u_s")
    child = [i for i in engine.runtime.list_instances() if i.parent_id == parent_id]
    assert child, "子实例应已启动"
    child_id = child[0].id
    child_task = engine.task.list_tasks(instance_id=child_id)[0]
    with pytest.raises(FloworkError):
        engine.task.complete(child_task.id, actor="u_c")  # 子内 boom 炸 → 子挂起+故障单
    assert engine.runtime.get_instance(child_id).state == "suspended"
    _open_incident_id(engine, child_id)

    engine.runtime.terminate_process(parent_id, actor="admin", reason="砍树")
    incidents = engine.runtime.list_incidents(instance_id=child_id)
    assert incidents[0]["state"] == "resolved"
    assert incidents[0]["resolution"] == "instance_terminated"
