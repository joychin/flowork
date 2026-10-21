"""skip 手术范围收口（2026-10-21 内核 review P0-1 回归钉子）。

修前 skip 对**全部**未完令牌生效：管理员想跳过一个故障节点，并行分支上
无关的人工审批待办被静默取消放行（审批链空洞）；激活期回滚形态下还会
重新执行被跳过的节点。收口后范围由 incident.activity_code 圈定。
"""

from __future__ import annotations

import uuid

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from flowork.persistence.orm import IncidentRow


def _parallel_world() -> ProcessEngine:
    """gate 并行扇出：side（人工审批）∥ waitW（等消息，声明式 on_exit 未注册）。

    完成 gate 后（命令一）side 待办与 waitW 令牌均已持久化；发布消息（命令二）
    → 命中 → finish_wait 执行声明式 on_exit「missing」未注册 → EngineExecutionError
    归因 waitW、整笔回滚、实例挂起——**故障节点自身持有未完令牌**的形态。
    """
    engine = ProcessEngine()
    definition = (
        FlowBuilder("skip_scope")
        .start()
        .user_task("gate", assignee="'u_g'")
        .user_task("side", assignee="'u_s'")
        .wait(
            "waitW",
            message={"name": "evt", "correlation_key": "'K'"},
            hooks={"on_exit": {"handler": "missing"}},
        )
        .user_task("after", assignee="'u_b'")
        .user_task("join", assignee="'u_j'", advance_policy="all")
        .end()
        .connect("start", "gate")
        .connect("gate", "side")
        .connect("gate", "waitW")
        .connect("side", "join")
        .connect("waitW", "after")
        .connect("after", "join")
        .connect("join", "end")
        .build()
    )
    engine.repository.deploy(definition)
    return engine


def _suspend_at_wait(engine: ProcessEngine) -> str:
    instance_id = engine.runtime.start_process("skip_scope", start_user="u_s")
    gate = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(gate.id, actor="u_g")
    with pytest.raises(FloworkError):
        # on_exit 未注册 → 挂起（发布侧 correlation_key 为裸键，非表达式）
        engine.messages.publish_message("evt", correlation_key="K", payload={"a": 1})
    assert engine.runtime.get_instance(instance_id).state == "suspended"
    return instance_id


def test_parallel_sibling_approval_survives_skip():
    """skip 只砍故障节点：并行分支上 side 的审批待办不被取消、照常可办。"""
    engine = _parallel_world()
    instance_id = _suspend_at_wait(engine)
    incident = engine.runtime.list_incidents(instance_id=instance_id)[0]
    assert incident["activity_code"] == "waitW"

    engine.runtime.resolve_incident(incident["id"], action="skip", actor="ops")

    assert engine.runtime.get_instance(instance_id).state == "running"
    codes = [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)]
    assert sorted(codes) == ["after", "side"]  # waitW 已随手术归档，side 存活
    for _ in range(5):  # side/after → all 汇聚 join → end
        tasks = engine.task.list_tasks(instance_id=instance_id)
        if not tasks:
            break
        engine.task.complete(tasks[0].id, actor=tasks[0].assignee)
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_skip_never_reexecutes_failed_node():
    """激活期回滚形态：skip 直接激活故障节点后继，故障节点不再执行。

    用 handler 仍炸的 service 复现（修前：重新进入故障节点 → resolve 命令
    自身抛错回滚，该故障单永远无法用 skip 解开）。
    """
    engine = ProcessEngine()
    definition = (
        FlowBuilder("skip_chain")
        .start()
        .user_task("a", assignee="'u_a'")
        .service_task("boom", "炸点", action="explode", params={})
        .user_task("after", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "boom")
        .connect("boom", "after")
        .connect("after", "end")
        .build()
    )
    engine.repository.deploy(definition)

    def explode(ctx, params):
        raise RuntimeError("外部接口炸了")

    engine.register_handler("explode", explode)
    instance_id = engine.runtime.start_process("skip_chain", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    with pytest.raises(FloworkError):
        engine.task.complete(task.id, actor="u_a")
    incident = engine.runtime.list_incidents(instance_id=instance_id)[0]
    assert incident["activity_code"] == "boom"

    engine.runtime.resolve_incident(incident["id"], action="skip", actor="ops")

    assert engine.runtime.get_instance(instance_id).state == "running"
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["after"]


def test_skip_refuses_incident_without_node_scope():
    """无节点定位的故障单拒绝 skip（修前会砍掉全部未完令牌）。"""
    engine = ProcessEngine()
    definition = (
        FlowBuilder("skip_no_scope")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("skip_no_scope", start_user="u_s")
    incident_id = uuid.uuid4().hex
    with engine.session_factory() as session:
        session.add(
            IncidentRow(
                id=incident_id,
                instance_id=instance_id,
                message="非节点作用域故障",
                error_class="FloworkError",
            )
        )
        session.commit()

    with pytest.raises(FloworkError, match="无节点定位"):
        engine.runtime.resolve_incident(incident_id, action="skip", actor="ops")
    # 实例与待办原样保留
    assert engine.runtime.get_instance(instance_id).state == "running"
    assert len(engine.task.list_tasks(instance_id=instance_id)) == 1
