"""表决行口径回归（spec v2.3 第十九轮）。

来源：2026-10-10 历史行口径走查——「谁真正办过这一票」谓词全库缺失，六个
名单类消费方清一色 ``outcome != "cancelled"``，转办/协办/咨询/自动通过行渗入
名单。走查实测的六种失效（本文件逐条钉正确语义）：

- 驳回复活名单吞行：单人节点挂起（ParticipantCountMismatchError）、
  多人会签应办数膨胀（违背「转办不增减应办总数」）；
- prev_task / node() 读到协办收口行或出口 auto_pass 行——**改变路由**；
- auto_pass_participated / previous 把协办人/咨询人/转办人算作已办——静默代投；
- 串行会签下一棒被协办行吞掉——running + 零待办 + 零故障单的无声死锁；
- 撤回闸门把下游协办意见算「已办理」——误拦。

对照组（test_participated_still_skips_real_voter 等）保证收口没有收紧过头：
真实表决行仍在名单里。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.node import MultiInstance
from flowork.engine.engine import ProcessEngine
from flowork.persistence.orm import ProcessInstanceRow
from flowork.tasks.facts import node_facts, prev_task_snapshot


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _linear(engine: ProcessEngine, code: str, *, a_assignee: str = "'u_a'") -> None:
    """a（单人）→ b（单人）→ end：驳回复活矩阵的基础形。"""
    engine.repository.deploy(
        FlowBuilder(code)
        .start()
        .user_task("a", assignee=a_assignee)
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )


def _ready(engine: ProcessEngine, instance_id: str) -> list[tuple[str, str]]:
    return [(t.activity_code, t.assignee) for t in engine.task.list_tasks(instance_id=instance_id)]


def _task_of(
    engine: ProcessEngine,
    instance_id: str,
    *,
    activity: str | None = None,
    assignee: str | None = None,
):
    return next(
        t
        for t in engine.task.list_tasks(instance_id=instance_id)
        if (activity is None or t.activity_code == activity)
        and (assignee is None or t.assignee == assignee)
    )


def _reject_at_b(engine: ProcessEngine, instance_id: str) -> None:
    task_b = _task_of(engine, instance_id, activity="b")
    engine.task.reject(task_b.id, actor="u_b", comment="退回")


# —— ① 驳回复活名单：非表决行不入列 ——


def test_reject_revival_after_transfer_revives_transferee_only(engine):
    """转办后驳回：只复活被转办人（票随人走），原办理人不回名单。"""
    _linear(engine, "rv1")
    instance_id = engine.runtime.start_process("rv1", start_user="s")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    moved = engine.task.transfer(task_a.id, to_user="u_d", actor="u_a")
    engine.task.complete(moved.id, actor="u_d")
    _reject_at_b(engine, instance_id)
    assert _ready(engine, instance_id) == [("a", "u_d")]
    assert engine.runtime.get_instance(instance_id).state == "running"


def test_reject_revival_after_open_assist_not_suspended(engine):
    """协办挂着、主办先完成（协办行 outcome=assist_cancel 落库最晚）后驳回：
    复活名单只有主办——此前名单吞入协办行，单人节点直接挂起。"""
    _linear(engine, "rv2")
    instance_id = engine.runtime.start_process("rv2", start_user="s")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.assist(task_a.id, ["u_h"], actor="u_a")  # 挂着不交
    engine.task.complete(task_a.id, actor="u_a")
    _reject_at_b(engine, instance_id)
    assert _ready(engine, instance_id) == [("a", "u_a")]
    assert engine.runtime.get_instance(instance_id).state == "running"


def test_reject_revival_after_completed_assist(engine):
    """协办已交还（outcome=assist）同样不入复活名单。"""
    _linear(engine, "rv3")
    instance_id = engine.runtime.start_process("rv3", start_user="s")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    assist = engine.task.assist(task_a.id, ["u_h"], actor="u_a")[0]
    engine.task.complete_assist(assist.id, actor="u_h")
    engine.task.complete(task_a.id, actor="u_a")
    _reject_at_b(engine, instance_id)
    assert _ready(engine, instance_id) == [("a", "u_a")]


def test_reject_revival_after_consult(engine):
    """咨询人交回意见（outcome 与真 agree 同形，靠 task_type 区分）不入名单。"""
    _linear(engine, "rv4")
    instance_id = engine.runtime.start_process("rv4", start_user="s")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    consult = engine.task.consult(task_a.id, ["u_co"], actor="u_a")[0]
    engine.task.complete_consult(consult.id, actor="u_co", opinion="agree")
    engine.task.complete(task_a.id, actor="u_a")
    _reject_at_b(engine, instance_id)
    assert _ready(engine, instance_id) == [("a", "u_a")]


def test_mi_transfer_revival_keeps_headcount(engine):
    """多人会签里转办一票后驳回：应办数保持 2（此前静默膨胀到 3）。"""
    engine.repository.deploy(
        FlowBuilder("rv5")
        .start()
        .user_task(
            "a", assignees=["'u_p1'", "'u_p2'"], multi_instance=MultiInstance(mode="parallel")
        )
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("rv5", start_user="s")
    tasks = {t.assignee: t for t in engine.task.list_tasks(instance_id=instance_id)}
    engine.task.transfer(tasks["u_p1"].id, to_user="u_p1_new", actor="u_p1")
    engine.task.complete(tasks["u_p2"].id, actor="u_p2")
    moved = _task_of(engine, instance_id, assignee="u_p1_new")
    engine.task.complete(moved.id, actor="u_p1_new")
    _reject_at_b(engine, instance_id)
    assert sorted(a for _, a in _ready(engine, instance_id)) == ["u_p1_new", "u_p2"]


# —— ② 判断面 prev_task / node()：最近一条表决行 ——


def test_prev_task_after_assist_cancel_routes_on_main_handler(engine):
    """协办挂账不毒化 prev_task：路由条件读到主办人的 agree（此前读到协办
    收口行 assist_cancel，条件不命中、流程改道）。"""
    engine.repository.deploy(
        FlowBuilder("pt1")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .flow(
            "a",
            "b",
            condition="prev_task.outcome == 'agree' && prev_task.assignee == 'u_a'",
        )
        .flow(
            "a",
            "end",
            condition="!(prev_task.outcome == 'agree' && prev_task.assignee == 'u_a')",
        )
        .connect("b", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("pt1", start_user="s")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.assist(task_a.id, ["u_h"], actor="u_a")  # 挂着不交
    engine.task.complete(task_a.id, actor="u_a")
    assert [code for code, _ in _ready(engine, instance_id)] == ["b"]


def test_prev_task_after_mi_exit_is_triggering_voter(engine):
    """会签一票出口：prev_task＝触发出口的投票人（此前＝被 auto_pass 的
    余票人，任何 any/N 票出口流程每次都读错）。"""
    engine.repository.deploy(
        FlowBuilder("pt2")
        .start()
        .user_task(
            "a",
            assignees=["'u_p1'", "'u_p2'"],
            multi_instance=MultiInstance(mode="parallel", approve_exit="task.approve_count >= 1"),
        )
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .flow("a", "b", condition="prev_task.assignee == 'u_p1'")
        .flow("a", "end", condition="prev_task.assignee != 'u_p1'")
        .connect("b", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("pt2", start_user="s")
    task_p1 = _task_of(engine, instance_id, assignee="u_p1")
    engine.task.complete(task_p1.id, actor="u_p1")
    with engine.session_factory() as session:
        assert prev_task_snapshot(session, instance_id) is not None
        snapshot = prev_task_snapshot(session, instance_id) or {}
        assert snapshot.get("assignee") == "u_p1" and snapshot.get("outcome") == "agree"
    assert [code for code, _ in _ready(engine, instance_id)] == ["b"]


def test_node_facts_latest_person_is_voter(engine):
    """node("a") 的最近轮人物＝表决行办理人（协办收口行不占位）。"""
    _linear(engine, "nf1")
    instance_id = engine.runtime.start_process("nf1", start_user="s")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.assist(task_a.id, ["u_h"], actor="u_a")
    engine.task.complete(task_a.id, actor="u_a")
    with engine.session_factory() as session:
        facts = node_facts(session, instance_id, "a")
    assert facts["assignee"] == "u_a" and facts["outcome"] == "agree"


# —— ③④ auto_pass 策略：成员资格＝表决过 ——


def _participated_flow(engine: ProcessEngine, code: str) -> str:
    engine.repository.deploy(
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task(
            "b",
            assignees=["'u_a'", "'u_h'"],
            multi_instance=MultiInstance(mode="parallel"),
            auto_pass_participated=True,
        )
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    return engine.runtime.start_process(code, start_user="s")


def test_participated_ignores_assistant(engine):
    """协办人不算已参与：b 会签照常给他待办（此前静默代投、实例直接完成）。"""
    instance_id = _participated_flow(engine, "ap1")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.assist(task_a.id, ["u_h"], actor="u_a")
    engine.task.complete(task_a.id, actor="u_a")
    assert sorted(a for _, a in _ready(engine, instance_id)) == ["u_h"]
    assert engine.runtime.get_instance(instance_id).state == "running"


def test_participated_ignores_consultant(engine):
    """咨询人不算已参与。"""
    instance_id = _participated_flow(engine, "ap2")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    consult = engine.task.consult(task_a.id, ["u_h"], actor="u_a")[0]
    engine.task.complete_consult(consult.id, actor="u_h", opinion="agree")
    engine.task.complete(task_a.id, actor="u_a")
    assert sorted(a for _, a in _ready(engine, instance_id)) == ["u_h"]


def test_participated_ignores_transferor(engine):
    """转办人不算已参与（第十九轮裁定②）：票由被转办人代为行使，
    转办人后续节点照常收到待办——多问一次人优于替人投一票。"""
    engine.repository.deploy(
        FlowBuilder("ap3")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_a'", auto_pass_participated=True)
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("ap3", start_user="s")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    moved = engine.task.transfer(task_a.id, to_user="u_d", actor="u_a")
    engine.task.complete(moved.id, actor="u_d")
    assert _ready(engine, instance_id) == [("b", "u_a")]
    assert engine.runtime.get_instance(instance_id).state == "running"


def test_participated_still_skips_real_voter(engine):
    """对照：真实表决过的人仍被跳过（收口没有收紧过头）。"""
    instance_id = _participated_flow(engine, "ap4")
    engine.task.complete(engine.task.list_tasks(instance_id=instance_id)[0].id, actor="u_a")
    assert _ready(engine, instance_id) == [("b", "u_h")]  # u_a 已表决 → 跳过


def test_auto_pass_previous_ignores_consultant(engine):
    """auto_pass_previous 的上一站名单＝表决行办理人（咨询人不算）。"""
    engine.repository.deploy(
        FlowBuilder("ap5")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_co'", auto_pass_previous=True)
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("ap5", start_user="s")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    consult = engine.task.consult(task_a.id, ["u_co"], actor="u_a")[0]
    engine.task.complete_consult(consult.id, actor="u_co", opinion="agree")
    engine.task.complete(task_a.id, actor="u_a")
    assert _ready(engine, instance_id) == [("b", "u_co")]


# —— ⑤ 串行会签下一棒：轮次消耗行（协办/咨询不算，transfer/auto_pass 算） ——


def test_sequential_mi_assist_does_not_swallow_next_turn(engine):
    """串行会签中途协办不吞下一棒（此前：remaining 被协办行清空 →
    running + 零待办 + 零故障单的无声死锁）。"""
    engine.repository.deploy(
        FlowBuilder("sq1")
        .start()
        .user_task(
            "a", assignees=["'u_p1'", "'u_h'"], multi_instance=MultiInstance(mode="sequential")
        )
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("sq1", start_user="s")
    task_p1 = _task_of(engine, instance_id, assignee="u_p1")
    assist = engine.task.assist(task_p1.id, ["u_h"], actor="u_p1")[0]
    engine.task.complete_assist(assist.id, actor="u_h")
    engine.task.complete(task_p1.id, actor="u_p1")
    assert _ready(engine, instance_id) == [("a", "u_h")]  # 下一棒仍是 u_h
    task_h = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task_h.id, actor="u_h")
    assert [code for code, _ in _ready(engine, instance_id)] == ["b"]


def test_sequential_mi_transfer_consumes_turn(engine):
    """对照：串行会签中转办算轮次消耗——转办人不再被重派（票在被转办人）。"""
    engine.repository.deploy(
        FlowBuilder("sq2")
        .start()
        .user_task(
            "a", assignees=["'u_p1'", "'u_h'"], multi_instance=MultiInstance(mode="sequential")
        )
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("sq2", start_user="s")
    task_p1 = _task_of(engine, instance_id, assignee="u_p1")
    moved = engine.task.transfer(task_p1.id, to_user="u_d", actor="u_p1")
    engine.task.complete(moved.id, actor="u_d")
    assert _ready(engine, instance_id) == [("a", "u_h")]  # u_p1 不重派，下一棒 u_h


# —— ⑥ 撤回闸门：下游协办意见不算进展 ——


def test_withdraw_gate_ignores_downstream_assist(engine):
    """下游只交过协办意见（主任务未办）不拦撤回（此前误拦且错误消息
    把协办写成「办理」）。"""
    engine.repository.deploy(
        FlowBuilder("wd1")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .user_task("c", assignee="'u_c'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "c")
        .connect("c", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("wd1", start_user="s")
    engine.task.complete(engine.task.list_tasks(instance_id=instance_id)[0].id, actor="u_a")
    task_b = _task_of(engine, instance_id, activity="b")
    assist = engine.task.assist(task_b.id, ["u_helper"], actor="u_b")[0]
    engine.task.complete_assist(assist.id, actor="u_helper")
    history = next(
        h for h in engine.history.task_history(instance_id=instance_id) if h.activity_code == "a"
    )
    engine.task.withdraw(history.task_id, actor="u_a")
    assert _ready(engine, instance_id) == [("a", "u_a")]


def test_withdraw_gate_still_blocks_real_downstream_vote(engine):
    """对照：下游真实表决仍拦撤回（收口没有放开过头）。"""
    engine.repository.deploy(
        FlowBuilder("wd2")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .user_task("c", assignee="'u_c'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "c")
        .connect("c", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("wd2", start_user="s")
    engine.task.complete(engine.task.list_tasks(instance_id=instance_id)[0].id, actor="u_a")
    task_b = _task_of(engine, instance_id, activity="b")
    engine.task.complete(task_b.id, actor="u_b")
    history = next(
        h for h in engine.history.task_history(instance_id=instance_id) if h.activity_code == "a"
    )
    with pytest.raises(Exception, match="下游已办理"):
        engine.task.withdraw(history.task_id, actor="u_a")


# —— ⑦ 无声死锁回归钉子：吞行后不应残留 running+零待办+零故障单 ——


def test_no_silent_deadlock_after_sequential_assist(engine):
    """走查发现的失效模式钉死：串行会签协办后实例不得停在
    running 且零待办（无声死锁）。"""
    engine.repository.deploy(
        FlowBuilder("sq3")
        .start()
        .user_task(
            "a", assignees=["'u_p1'", "'u_h'"], multi_instance=MultiInstance(mode="sequential")
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("sq3", start_user="s")
    task_p1 = _task_of(engine, instance_id, assignee="u_p1")
    engine.task.assist(task_p1.id, ["u_h"], actor="u_p1")  # 挂着不交
    engine.task.complete(task_p1.id, actor="u_p1")
    with engine.session_factory() as session:
        state = session.get(ProcessInstanceRow, instance_id).state
    ready = engine.task.list_tasks(instance_id=instance_id, state="ready")
    assert not (state == "running" and not ready), "串行会签吞下一棒＝无声死锁（已修复，不得回归）"
    assert _ready(engine, instance_id) == [("a", "u_h")]
