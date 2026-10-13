"""事件信号补全（2026-10-13 事件 review A3/A4/A6/A7）。

- 取消族统一咽喉：cancel_token 逐任务发 task_cancelled、节点级
  node_cancelled(by=原因)——撤回/终态/竞速/驳回/MI 收工/故障跳过六路验收
  （修前仅故障跳过一路手发，其余各路对订阅方完全失明）；
- auto_pass 四处归档统一发 task_auto_passed（reason 区分），策略命中的
  幻影任务不再发 task_created（待办通知不误报）；
- 对称补齐：unclaim / complete_assist 事件；
- message_received 三路统一（缓冲/立即关联/消息启动/correlate 入口）。
"""

from __future__ import annotations

import pytest

from flowork.api import FlowBuilder, FloworkError, MultiInstance, ProcessEngine
from flowork.definitions.definition import StartMessageDecl


def _events(engine: ProcessEngine, instance_id: str, name: str) -> list[dict]:
    return [
        e.payload for e in engine.history.events(instance_id, limit=200) if e.event_name == name
    ]


def _names(engine: ProcessEngine, instance_id: str) -> list[str]:
    return [e.event_name for e in engine.history.events(instance_id, limit=200)]


def _flow_two_step(code: str) -> FlowBuilder:
    return (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u1'")
        .user_task("b", assignee="'u2'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
    )


# —— 取消族六路 ——


def test_withdraw_cancels_downstream_with_cause():
    """撤回：下游令牌取消发 node_cancelled(by=withdraw)+task_cancelled；a 自身不发。"""
    engine = ProcessEngine()
    engine.repository.deploy(_flow_two_step("sig_withdraw").build())
    iid = engine.runtime.start_process("sig_withdraw", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]
    engine.task.complete(task.id, actor="u1")
    engine.task.withdraw(task.id, actor="u1")
    cancelled = _events(engine, iid, "node_cancelled")
    assert [(p["activity_code"], p["by"]) for p in cancelled] == [("b", "withdraw")]
    tasks = _events(engine, iid, "task_cancelled")
    assert len(tasks) == 1 and tasks[0]["activity_code"] == "b"
    assert tasks[0]["assignee"] == "u2"
    engine.close()


def test_terminate_emits_node_cancelled_with_cause():
    engine = ProcessEngine()
    engine.repository.deploy(_flow_two_step("sig_term").build())
    iid = engine.runtime.start_process("sig_term", start_user="u_s")
    engine.task.complete(engine.task.list_tasks(instance_id=iid)[0].id, actor="u1")
    engine.runtime.terminate_process(iid, actor="u_admin")
    cancelled = _events(engine, iid, "node_cancelled")
    # by 统一语义标签（2026-10-13 事件 review 复核批）：修前混传终态值 "terminated"
    assert [(p["activity_code"], p["by"]) for p in cancelled] == [("b", "instance_terminated")]
    assert _events(engine, iid, "task_cancelled")
    engine.close()


def test_racing_arm_cancelled_with_cause():
    """any 汇聚竞速收尾：先到臂激活 end，未完臂取消（by=racing）。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("sig_race")
        .start()
        .user_task("a1", assignee="'u1'")
        .user_task("a2", assignee="'u2'")
        .end()
        .connect("start", "a1")
        .connect("start", "a2")
        .connect("a1", "end")
        .connect("a2", "end")
        .build()
    )
    iid = engine.runtime.start_process("sig_race", start_user="u_s")
    engine.task.complete(engine.task.list_tasks(instance_id=iid)[0].id, actor="u1")
    cancelled = _events(engine, iid, "node_cancelled")
    assert [(p["activity_code"], p["by"]) for p in cancelled] == [("a2", "racing")]
    assert _events(engine, iid, "task_cancelled")[0]["assignee"] == "u2"
    engine.close()


def test_reject_cancels_sibling_and_auto_passes_pending():
    """驳回：兄弟分支取消（by=reject）；本令牌剩余待办归档发
    task_auto_passed(reason=node_rejected)。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("sig_reject")
        .start()
        .user_task(
            "p",
            assignees=["'u1'", "'u2'"],
            multi_instance=MultiInstance(
                mode="parallel", disapprove_exit="task.disapprove_count >= 1"
            ),
        )
        .user_task("s", assignee="'u3'")
        .end()
        .connect("start", "p")
        .connect("start", "s")
        .connect("p", "end")
        .connect("s", "end")
        .build()
    )
    iid = engine.runtime.start_process("sig_reject", start_user="u_s")
    tasks = {t.assignee: t for t in engine.task.list_tasks(instance_id=iid)}
    engine.task.reject(tasks["u1"].id, actor="u1", comment="不同意")
    cancelled = _events(engine, iid, "node_cancelled")
    assert ("s", "reject") in [(p["activity_code"], p["by"]) for p in cancelled]
    auto_passed = _events(engine, iid, "task_auto_passed")
    assert any(p["reason"] == "node_rejected" and p["assignee"] == "u2" for p in auto_passed)
    engine.close()


def test_mi_early_completion_cancels_remaining_children():
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("mi_child_sig")
        .start()
        .user_task("review", assignee="'u_worker'")
        .end()
        .connect("start", "review")
        .connect("review", "end")
        .build()
    )
    engine.repository.deploy(
        FlowBuilder("sig_mi")
        .start()
        .call_activity(
            "sub",
            process_code="mi_child_sig",
            multi_instance={
                "subjects": ["'u_a'", "'u_b'", "'u_c'"],
                "completion": "child_completed >= 1",
            },
        )
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    iid = engine.runtime.start_process("sig_mi", start_user="u_s")
    # 三子各停在 review；完成其一 → completion 命中 → 剩余子令牌取消（事件落子实例）
    children = [i for i in engine.history.instances() if i.parent_id == iid]
    for child in children:
        tasks = engine.task.list_tasks(instance_id=child.id)
        if tasks:
            engine.task.complete(tasks[0].id, actor="u_worker")
            break
    causes: list[tuple[str, str]] = []
    for child in children:
        causes += [
            (p["by"], p["activity_code"]) for p in _events(engine, child.id, "node_cancelled")
        ]
    assert ("multi_instance_completion", "review") in causes
    assert len(causes) == 2  # 恰两个剩余子被取消
    engine.close()


def test_incident_skip_emits_single_node_cancelled():
    """故障跳过：by=incident:skip 且每节点恰一条（旧手发已删，防双发）。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("sig_skip")
        .start()
        .user_task("a", assignee="'u1'")
        .user_task("b", assignee="'u2'", hooks={"on_enter": {"handler": "nope"}})
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    iid = engine.runtime.start_process("sig_skip", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]
    with pytest.raises(FloworkError):  # b 的钩子未注册 → 技术故障
        engine.task.complete(task.id, actor="u1")
    # skip 会按路由重进 b——先补注册 handler，隔离「取消咽喉」这一被测对象
    engine.register_handler("nope", lambda ctx, params: None)
    incident = engine.runtime.list_incidents(iid)[0]
    engine.runtime.resolve_incident(incident["id"], action="skip", actor="u_admin")
    cancelled = _events(engine, iid, "node_cancelled")
    assert [(p["activity_code"], p["by"]) for p in cancelled] == [("a", "incident:skip")]
    engine.close()


# —— auto_pass 四处 ——


def test_auto_pass_strategy_suppresses_phantom_created():
    """策略命中：建了即归档的幻影任务不发 task_created，发 task_auto_passed。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("sig_strategy")
        .start()
        .user_task("a", assignee="'u_s'", auto_pass_originator=True)
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    iid = engine.runtime.start_process("sig_strategy", start_user="u_s")  # 发起人=办理人
    names = _names(engine, iid)
    assert "task_created" not in names  # 幻影抑制（修前误发待办通知）
    passed = _events(engine, iid, "task_auto_passed")
    assert [(p["reason"], p["assignee"]) for p in passed] == [("strategy", "u_s")]
    assert "node_exited" in names  # 全员自动通过 → 节点立即完成
    engine.close()


def test_auto_pass_exit_reached():
    """会签出口达成：剩余待办归档发 task_auto_passed(reason=exit_reached)。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("sig_exit")
        .start()
        .user_task(
            "a",
            assignees=["'u1'", "'u2'"],
            multi_instance=MultiInstance(mode="parallel", approve_exit="task.approve_count >= 1"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    iid = engine.runtime.start_process("sig_exit", start_user="u_s")
    tasks = {t.assignee: t for t in engine.task.list_tasks(instance_id=iid)}
    engine.task.complete(tasks["u1"].id, actor="u1")
    passed = _events(engine, iid, "task_auto_passed")
    assert [(p["reason"], p["assignee"]) for p in passed] == [("exit_reached", "u2")]
    engine.close()


def test_admin_finish_auto_pass():
    engine = ProcessEngine()
    engine.repository.deploy(_flow_two_step("sig_admin").build())
    iid = engine.runtime.start_process("sig_admin", start_user="u_s")
    engine.task.finish_instance(iid, actor="u_admin")
    passed = _events(engine, iid, "task_auto_passed")
    assert [(p["reason"], p["assignee"]) for p in passed] == [("admin_finish", "u1")]
    assert _events(engine, iid, "process_finished_by_admin")
    engine.close()


# —— 对称补齐 ——


def test_unclaim_event():
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("sig_unclaim")
        .start()
        .user_task("a", candidate_users=["'u_c'"])
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    iid = engine.runtime.start_process("sig_unclaim", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]
    engine.task.claim(task.id, "u_c")
    engine.task.unclaim(task.id, "u_c")
    events = _events(engine, iid, "task_unclaimed")
    assert len(events) == 1
    assert events[0]["assignee"] == "u_c" and events[0]["activity_code"] == "a"
    engine.close()


def test_assist_finished_event():
    engine = ProcessEngine()
    engine.repository.deploy(_flow_two_step("sig_assist").build())
    iid = engine.runtime.start_process("sig_assist", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]
    assists = engine.task.assist(task.id, ["u9"], actor="u1")
    engine.task.complete_assist(assists[0].id, actor="u9", comment="意见")
    events = _events(engine, iid, "task_assist_finished")
    assert len(events) == 1
    assert events[0]["root_task_id"] == task.id and events[0]["assignee"] == "u9"
    engine.close()


# —— message_received 三路统一 ——


def _global_names_asc(engine: ProcessEngine, limit: int = 200) -> list[str]:
    """全局事件名（时间正序）——message_received 是全局事件（无实例维度）。"""
    return [e.event_name for e in reversed(engine.history.events(limit=limit))]


def test_message_received_on_buffer_path():
    engine = ProcessEngine()
    assert engine.messages.publish_message("m0", "k0", {"x": 1}) is False  # 无人在等
    received = [
        e.payload for e in engine.history.events(limit=50) if e.event_name == "message_received"
    ]
    assert len(received) == 1
    assert received[0]["message_id"]  # 防重追踪不断链（修前载荷无此键）
    engine.close()


def test_message_received_before_correlated_on_immediate_hit():
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("sig_wait")
        .start()
        .wait("w", message={"name": "m1"})
        .end()
        .connect("start", "w")
        .connect("w", "end")
        .build()
    )
    engine.runtime.start_process("sig_wait", start_user="u_s")
    assert engine.messages.publish_message("m1", "", {"v": 1}) is True
    names = _global_names_asc(engine)
    assert "message_received" in names  # 修前立即关联路径不发 received
    assert names.index("message_received") < names.index("message_correlated")
    engine.close()


def test_message_received_on_message_start():
    engine = ProcessEngine()
    definition = (
        FlowBuilder("sig_starter")
        .start()
        .user_task("work", assignee="'u_worker'")
        .end()
        .connect("start", "work")
        .connect("work", "end")
        .build()
    )
    definition.start_on_message = [
        StartMessageDecl(name="m2", correlation_key="'k2'", start_user="'u_s'")
    ]
    engine.repository.deploy(definition)
    assert engine.messages.publish_message("m2", "k2", {}) is True
    received = [
        e.payload for e in engine.history.events(limit=50) if e.event_name == "message_received"
    ]
    assert len(received) == 1 and received[0]["name"] == "m2"  # 修前消息启动路径不发
    engine.close()


def test_message_received_on_correlate_entry():
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("sig_corr")
        .start()
        .wait("w", message={"name": "m3"})
        .end()
        .connect("start", "w")
        .connect("w", "end")
        .build()
    )
    engine.runtime.start_process("sig_corr", start_user="u_s")
    receipt = engine.messages.correlate_message("m3", "", {"v": 2})
    assert receipt is not None and receipt["matched"]
    assert "message_received" in _global_names_asc(engine)  # 修前 correlate 入口零 received
    engine.close()


# —— 复核批（2026-10-13 事件 review 复核）：管理员强制完成 ——


def test_admin_finish_guard_and_node_exited():
    """finish_instance：终态守卫拒绝重复调用；强制完成的令牌补发 node_exited。"""
    engine = ProcessEngine()
    engine.repository.deploy(_flow_two_step("sig_admin_finish").build())
    iid = engine.runtime.start_process("sig_admin_finish", start_user="u_s")
    engine.task.finish_instance(iid, actor="boss")

    # 修前：强制完成的令牌有 node_entered 无 node_exited（订阅方配对断裂）
    entered = _events(engine, iid, "node_entered")
    exited = _events(engine, iid, "node_exited")
    assert {p["activity_code"] for p in entered} <= {p["activity_code"] for p in exited}

    # 修前：对已终态实例再调会发假 process_finished_by_admin（complete_instance
    # 静默跳过但事件无条件发）
    with pytest.raises(FloworkError):
        engine.task.finish_instance(iid, actor="boss")
    assert len(_events(engine, iid, "process_finished_by_admin")) == 1
    engine.close()


# —— 复核批续（2026-10-13 事件 review 复核批）：两级事件语义与载荷 ——


def test_admin_finish_two_layer_terminal_events():
    """两级语义（成文）：process_completed=状态事实，process_finished_by_admin
    =管理动作留痕且带 end_status——终态契约始终是前者三件套。"""
    engine = ProcessEngine()
    engine.repository.deploy(_flow_two_step("sig_two_layer").build())
    iid = engine.runtime.start_process("sig_two_layer", start_user="u_s")
    engine.task.finish_instance(iid, actor="boss")
    completed = _events(engine, iid, "process_completed")
    finished = _events(engine, iid, "process_finished_by_admin")
    assert len(completed) == 1 and len(finished) == 1
    assert finished[0]["end_status"] == "completed" and finished[0]["actor"] == "boss"
    engine.close()


def test_node_cancelled_carries_actor():
    """状态层 node_cancelled 也带 actor（复核批）：撤回/终止等取消的归因
    不再只有动作层才有。"""
    engine = ProcessEngine()
    engine.repository.deploy(_flow_two_step("sig_nc_actor").build())
    iid = engine.runtime.start_process("sig_nc_actor", start_user="u_s")
    engine.task.complete(engine.task.list_tasks(instance_id=iid)[0].id, actor="u1")
    engine.runtime.terminate_process(iid, actor="boss")
    cancelled = _events(engine, iid, "node_cancelled")
    assert cancelled and all(p["actor"] == "boss" for p in cancelled)
    engine.close()
