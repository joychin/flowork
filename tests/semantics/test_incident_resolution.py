"""P1-4（kernel-v2 spec §8/§16）：错误二分落地——resolve 扩 retry/skip/fail 测试。

业务失败（驳回/契约拒收）不产生故障单；技术故障停等人工处置，五种动作
resume/cancel/retry/skip/fail 各就各位；根因未修好允许复发。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError


def _engine_with_failing_action() -> ProcessEngine:
    engine = ProcessEngine()
    definition = (
        FlowBuilder("incident_flow")
        .start()
        .user_task("a", assignee="u_a")
        .service_task("boom", "会炸的步骤", action="explode", params={})
        .user_task("after", assignee="u_b")
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
    return engine


def _open_incident(engine: ProcessEngine, instance_id: str) -> str:
    incidents = engine.runtime.list_incidents(instance_id=instance_id)
    assert incidents, "应有 open Incident"
    return incidents[0]["id"]


def _bring_to_boom(engine: ProcessEngine) -> str:
    instance_id = engine.runtime.start_process("incident_flow", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    with pytest.raises(FloworkError):
        engine.task.complete(task.id, actor="u_a")  # boom 炸 → Incident + suspended
    assert engine.runtime.get_instance(instance_id).state == "suspended"
    return instance_id


def test_retry_resumes_then_redo_succeeds():
    """技术故障路径①：handler 失败→故障单挂起（命令已回滚，任务 a 仍待办）；
    retry 恢复运行——修复 handler 后重办同一任务即完成重试。"""
    engine = _engine_with_failing_action()
    instance_id = _bring_to_boom(engine)
    incident_id = _open_incident(engine, instance_id)

    def explode_fixed(ctx, params):
        ctx.variables["fixed"] = True

    engine.register_handler("explode", explode_fixed, replace=True)
    engine.runtime.resolve_incident(incident_id, action="retry", actor="ops")
    assert engine.runtime.get_instance(instance_id).state == "running"
    # 失败命令整体回滚——任务 a 仍在，重办即重试
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a")
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["after"]


def test_skip_moves_token_past_current_node():
    """技术故障路径②：skip——当前停住节点的未完令牌视为完成，路由推进后继。"""
    engine = _engine_with_failing_action()
    instance_id = _bring_to_boom(engine)
    incident_id = _open_incident(engine, instance_id)

    def explode_fixed(ctx, params):
        pass

    engine.register_handler("explode", explode_fixed, replace=True)
    engine.runtime.resolve_incident(incident_id, action="skip", actor="ops")
    assert engine.runtime.get_instance(instance_id).state == "running"
    # 失败回滚后令牌停在 a——skip 视 a 为完成：boom 执行（已修复）→ after
    tasks = [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)]
    assert tasks == ["after"]


def test_fail_terminates_instance():
    """技术故障路径③：fail——管理员判定不可恢复，终态 terminated。"""
    engine = _engine_with_failing_action()
    instance_id = _bring_to_boom(engine)
    incident_id = _open_incident(engine, instance_id)

    engine.runtime.resolve_incident(incident_id, action="fail", actor="ops")
    assert engine.runtime.get_instance(instance_id).state == "terminated"
    assert engine.task.list_tasks(instance_id=instance_id) == []


def test_business_failure_produces_no_incident():
    """业务失败（契约拒收）不产生故障单——错误二分的另一半（spec §8）。"""
    from flowork.definitions.contract import InputContractError

    engine = ProcessEngine()
    definition = (
        FlowBuilder("biz_flow")
        .start()
        .user_task("a", assignee="u_a")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    definition.input = {
        "type": "object",
        "properties": {"amount": {"type": "number"}},
        "required": ["amount"],
    }
    engine.repository.deploy(definition)
    with pytest.raises(InputContractError):
        engine.runtime.start_process("biz_flow", start_user="u_s", variables={})
    assert engine.runtime.list_incidents() == []


def test_unknown_action_rejected():
    engine = _engine_with_failing_action()
    instance_id = _bring_to_boom(engine)
    with pytest.raises(ValueError, match="retry/skip/fail"):
        engine.runtime.resolve_incident(_open_incident(engine, instance_id), action="nonsense")
