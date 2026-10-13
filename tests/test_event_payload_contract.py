"""事件载荷契约（2026-10-13 事件 review B1/B2/B3/B4）。

来源：task 族事件此前只有 task_id/assignee/actor——消费者定位节点须反查
wf_task_history，且转办克隆的 task_created 与主路径载荷形状不一致；
complete/reject 无 comment（驳回理由事件面丢失）、incident_raised 无
incident_id（与 resolved 无法配对）、node_* 无 token_id（并行分支不可区分）、
process_started 无版本号、sub_process_started 只有实例 id、timeout_warned
只有 job_id。本契约逐字段钉死，防形状回退。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from flowork.api import EngineConfig, FlowBuilder, FloworkError, ProcessEngine
from flowork.definitions.node import TaskDeadline


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


def test_complete_and_reject_carry_comment_and_locator():
    """B1/B2：completed/rejected 带 activity_code/task_type/comment。"""
    engine = _engine_with_flow()
    instance_id = engine.runtime.start_process("evt_payload", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u1", comment="同意，没问题")
    completed = _events_of(engine, instance_id, "task_completed")
    assert completed[0]["comment"] == "同意，没问题"
    assert completed[0]["activity_code"] == "a"
    assert completed[0]["task_type"] == "approval"

    engine2 = _engine_with_flow()
    iid2 = engine2.runtime.start_process("evt_payload", start_user="u_s")
    task2 = engine2.task.list_tasks(instance_id=iid2)[0]
    engine2.task.reject(task2.id, actor="u1", comment="金额不符")
    rejected = _events_of(engine2, iid2, "task_rejected")
    assert rejected[0]["comment"] == "金额不符"  # 修前驳回理由事件面丢失
    assert rejected[0]["activity_code"] == "a"
    engine.close()
    engine2.close()


def test_node_events_carry_token_id():
    """B4：node_entered/exited 带 token_id——并行分支同节点多次激活可区分。"""
    engine = _engine_with_flow()
    instance_id = engine.runtime.start_process("evt_payload", start_user="u_s")
    engine.task.complete(engine.task.list_tasks(instance_id=instance_id)[0].id, actor="u1")
    entered = _events_of(engine, instance_id, "node_entered")
    exited = _events_of(engine, instance_id, "node_exited")
    assert all(p["token_id"] for p in entered)
    assert all(p["token_id"] for p in exited)
    # 节点对齐：a 的进入与退出同一 token
    a_enter = next(p for p in entered if p["activity_code"] == "a")
    a_exit = next(p for p in exited if p["activity_code"] == "a")
    assert a_enter["token_id"] == a_exit["token_id"]
    engine.close()


def test_process_started_carries_version():
    """B4：process_started 带实例钉住的 major/revision（修前版本事件面不可见）。"""
    engine = _engine_with_flow()
    instance_id = engine.runtime.start_process("evt_payload", start_user="u_s")
    started = _events_of(engine, instance_id, "process_started")[0]
    assert started["major"] == 1 and started["revision"] == 1
    engine.close()


def test_sub_process_started_carries_locator():
    """B4：sub_process_started 带子定义 code 与父节点（修前只有实例 id）。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("payload_child")
        .start()
        .user_task("w", assignee="'u1'")
        .end()
        .connect("start", "w")
        .connect("w", "end")
        .build()
    )
    engine.repository.deploy(
        FlowBuilder("payload_parent")
        .start()
        .call_activity("sub", process_code="payload_child")
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    iid = engine.runtime.start_process("payload_parent", start_user="u_s")
    event = _events_of(engine, iid, "sub_process_started")[0]
    assert event["child_definition_code"] == "payload_child"
    assert event["parent_node"] == "sub"
    engine.close()


def test_incident_raised_carries_incident_id():
    """B3：incident_raised 带 incident_id——与 incident_resolved 可配对。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("payload_inc")
        .start()
        .user_task("a", assignee="'u1'")
        .user_task("b", assignee="'u2'", hooks={"on_enter": {"handler": "nope"}})
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    iid = engine.runtime.start_process("payload_inc", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]
    with pytest.raises(FloworkError):
        engine.task.complete(task.id, actor="u1")
    raised = _events_of(engine, iid, "incident_raised")[0]
    assert raised["incident_id"]  # 修前载荷无此键，故障发生与修复无法配对
    # 错误归因（复核批）：钩子失败的 incident_raised 带节点定位——修前只能
    # 解析 message 文本
    assert raised["activity_code"] == "b"
    incident = engine.runtime.list_incidents(iid)[0]
    assert raised["incident_id"] == incident["id"]
    engine.close()


def test_timeout_warned_carries_task_locator():
    """B4：timeout_warned 带 task_id/activity_code（修前只有 job_id，定位要反查）。"""
    clock = {"now": datetime(2026, 1, 1, 0, 0, 0)}

    def fake_now() -> datetime:
        return clock["now"]

    engine = ProcessEngine(EngineConfig(now=fake_now))
    engine.repository.deploy(
        FlowBuilder("payload_warn")
        .start()
        .user_task(
            "a",
            assignee="'u1'",
            deadlines=[TaskDeadline(after_minutes=60, action="notify")],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    iid = engine.runtime.start_process("payload_warn", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]
    clock["now"] = clock["now"] + timedelta(minutes=61)
    engine.jobs.tick()
    warned = _events_of(engine, iid, "timeout_warned")
    assert warned and warned[0]["task_id"] == task.id
    assert warned[0]["activity_code"] == "a"
    engine.close()


# —— 复核批（2026-10-13 事件 review 复核）：克隆 actor 归因 / 派生事件定位 / 取消归因 / 撤回键集 ——


def test_task_created_actor_attribution():
    """克隆 task_created 的 actor=发起人（修前误记被指派人）；主路径 actor=None 键集对齐。"""
    engine = _engine_with_flow()
    instance_id = engine.runtime.start_process("evt_payload", start_user="u_s")
    normal = _events_of(engine, instance_id, "task_created")[0]
    assert normal["actor"] is None  # 引擎解析产生：无操作人，但键集与克隆路径一致
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.transfer(task.id, to_user="u9", actor="u1")
    clone = _events_of(engine, instance_id, "task_created")[-1]
    assert clone["actor"] == "u1"  # 修前 == "u9"（被转办人），归因颠倒
    engine.close()


def test_derived_task_events_carry_activity_code():
    """assist/consult/countersigner 三个派生事件带 activity_code——修前只有 task_id。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("payload_derived")
        .start()
        .user_task("a", assignee="'u1'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    iid = engine.runtime.start_process("payload_derived", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]

    engine.task.assist(task.id, users=["u3"], actor="u1")
    assert _events_of(engine, iid, "task_assist_created")[0]["activity_code"] == "a"

    engine.task.consult(task.id, users=["u4"], actor="u1")
    assert _events_of(engine, iid, "task_consult_created")[0]["activity_code"] == "a"
    consult = engine.task.list_tasks(instance_id=iid, state="ready")
    engine.task.complete_consult(consult[-1].id, actor="u4", opinion="agree")
    finished = _events_of(engine, iid, "task_consult_finished")[0]
    assert finished["activity_code"] == "a" and finished["actor"] == "u4"

    engine.task.complete(task.id, actor="u1")  # 主任务收口 → 协办连带取消
    cancelled = _events_of(engine, iid, "task_assist_cancelled")[0]
    assert cancelled["activity_code"] == "a" and cancelled["actor"] == "u1"
    engine.close()


def test_countersigner_event_carries_activity_code():
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("payload_cs")
        .start()
        .user_task(
            "sign",
            assignees=["'u1'", "'u2'"],
            multi_instance={"mode": "parallel"},
        )
        .end()
        .connect("start", "sign")
        .connect("sign", "end")
        .build()
    )
    iid = engine.runtime.start_process("payload_cs", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]
    engine.task.add_countersigner(task.id, users=["u3"], actor="u1")
    added = _events_of(engine, iid, "task_countersigner_added")[0]
    assert added["activity_code"] == "sign"  # 修前缺节点定位
    engine.close()


def test_task_cancelled_carries_actor():
    """取消连带的 task_cancelled 归因发起人（修前一律 system、历史 acted_by 也 system）。"""
    engine = _engine_with_flow()
    instance_id = engine.runtime.start_process("evt_payload", start_user="u_s")
    engine.runtime.terminate_process(instance_id, actor="boss")
    cancelled = _events_of(engine, instance_id, "task_cancelled")
    assert cancelled and all(p["actor"] == "boss" for p in cancelled)
    history = engine.history.task_history(instance_id)
    assert all(h.acted_by == "boss" for h in history if h.outcome == "cancelled")
    engine.close()


def test_withdrawn_event_key_set_unified():
    """两分支键集对齐：重建分支补 token_id/vote_retracted（修前只有 new_token）。"""
    engine = _engine_with_flow()
    instance_id = engine.runtime.start_process("evt_payload", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u1", comment="ok")
    engine.task.withdraw(task.id, actor="u1", comment="撤回重办")
    withdrawn = _events_of(engine, instance_id, "task_withdrawn")[0]
    assert withdrawn["vote_retracted"] is False
    assert withdrawn["token_id"]  # 被撤回的原令牌（修前缺）
    assert withdrawn["new_token"]
    engine.close()


def test_escalated_event_carries_locator():
    """task_escalated 带 activity_code/task_type（修前只有 task_id/from/to）。"""
    clock = {"now": datetime(2026, 1, 1, 0, 0, 0)}

    def fake_now() -> datetime:
        return clock["now"]

    engine = ProcessEngine(EngineConfig(now=fake_now))
    engine.repository.deploy(
        FlowBuilder("payload_esc")
        .start()
        .user_task(
            "a",
            assignee="'u1'",
            deadlines=[TaskDeadline(after_minutes=60, action="escalate", escalate_to="'u9'")],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    iid = engine.runtime.start_process("payload_esc", start_user="u_s")
    clock["now"] = clock["now"] + timedelta(minutes=61)
    engine.jobs.tick()
    escalated = _events_of(engine, iid, "task_escalated")
    assert escalated and escalated[0]["activity_code"] == "a"
    assert escalated[0]["task_type"] == "approval"
    engine.close()


def test_sub_process_completed_signals_parent():
    """复核批：子实例完结时父侧发 sub_process_completed（修前只有 started，
    挂起期唤醒落空更无痕）——载荷带子定义/终态/父节点。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("payload_child2")
        .start()
        .user_task("w", assignee="'u1'")
        .end()
        .connect("start", "w")
        .connect("w", "end")
        .build()
    )
    engine.repository.deploy(
        FlowBuilder("payload_parent2")
        .start()
        .call_activity("sub", process_code="payload_child2")
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    iid = engine.runtime.start_process("payload_parent2", start_user="u_s")
    child = next(i for i in engine.history.instances() if i.parent_id == iid)
    task = engine.task.list_tasks(instance_id=child.id)[0]
    engine.task.complete(task.id, actor="u1")

    done = _events_of(engine, iid, "sub_process_completed")
    assert len(done) == 1
    assert done[0]["sub_instance_id"] == child.id
    assert done[0]["child_definition_code"] == "payload_child2"
    assert done[0]["end_status"] == "completed"
    assert done[0]["parent_node"] == "sub"
    assert engine.runtime.get_instance(iid).state == "completed"
    engine.close()


def test_suspension_emits_process_suspended_and_resumed():
    """复核批：挂起/恢复与 pause/resume 对称——incident 挂起补
    process_suspended（带故障单与节点定位），resolve 恢复补 process_resumed。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("payload_susp")
        .start()
        .user_task("a", assignee="'u1'")
        .user_task("b", assignee="'u2'", hooks={"on_enter": {"handler": "nope"}})
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    iid = engine.runtime.start_process("payload_susp", start_user="u_s")
    with pytest.raises(FloworkError):
        engine.task.complete(  # 推进到 b → 钩子未注册 → 回滚 + 挂起
            engine.task.list_tasks(instance_id=iid)[0].id, actor="u1"
        )
    suspended = _events_of(engine, iid, "process_suspended")
    assert len(suspended) == 1
    assert suspended[0]["activity_code"] == "b"  # 错误归因联动
    assert suspended[0]["incident_id"]

    incident = engine.runtime.list_incidents(iid)[0]
    engine.runtime.resolve_incident(incident["id"], action="resume", actor="boss")
    resumed = _events_of(engine, iid, "process_resumed")
    assert len(resumed) == 1 and resumed[0]["reason"] == "incident:resume"
    assert resumed[0]["actor"] == "boss"
    assert engine.runtime.get_instance(iid).state == "running"  # a 回到可办
    engine.close()
