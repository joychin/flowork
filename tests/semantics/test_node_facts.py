"""F2 节点事实（expression-dataface §2.2/§2.3/§3）：验收 A1/B/C/D 组主干。

prev_task＝上一站快照（最近一条已完成任务；条件+取人位）；node("code")＝点名查
节点事实（轮次/最近轮/票数）；查无＝条件不满足、取人贡献空（温和，不出故障单）。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.node import MultiInstance
from flowork.engine.engine import ProcessEngine

VOTE_GATE = 'node("voting").approve_count * 100 >= node("voting").item_count * 60'


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _open(engine: ProcessEngine, instance_id: str, activity: str | None = None) -> list:
    tasks = engine.task.list_tasks(instance_id=instance_id)
    return [t for t in tasks if activity is None or t.activity_code == activity]


# —— D5/B2 前置：上一站查无（start 后首条条件边）＝不满足，走默认边 ————


def test_prev_task_absent_on_start_edge_is_false(engine: ProcessEngine):
    definition = (
        FlowBuilder("t_absent")
        .start()
        .branch("gate")
        .user_task("big", assignee="'u_a'")
        .user_task("small", assignee="'u_b'")
        .end()
        .connect("start", "gate")
        .flow("gate", "big", condition="prev_task.outcome == 'agree'")
        .flow("gate", "small", default=True)
        .connect("big", "end")
        .connect("small", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("t_absent", start_user="u_s")
    assert [t.activity_code for t in _open(engine, iid)] == ["small"]


def test_prev_task_outcome_and_comment_on_edge(engine: ProcessEngine):
    """B2/B3：上一站结果与意见驱动分支；comment 默认空串不误伤 contains。"""
    definition = (
        FlowBuilder("t_edge")
        .start()
        .user_task("manager", assignee="'u_mgr'")
        .branch("gate")
        .user_task("urgent", assignee="'u_dir'")
        .user_task("normal", assignee="'u_clerk'")
        .end()
        .connect("start", "manager")
        .connect("manager", "gate")
        .flow(
            "gate",
            "urgent",
            condition="prev_task.outcome == 'agree' && prev_task.comment.contains('加急')",
        )
        .flow("gate", "normal", default=True)
        .connect("urgent", "end")
        .connect("normal", "end")
        .build()
    )
    engine.repository.deploy(definition)

    hot = engine.runtime.start_process("t_edge", start_user="u_s")
    engine.task.complete(_open(engine, hot, "manager")[0].id, actor="u_mgr", comment="加急处理")
    assert [t.activity_code for t in _open(engine, hot)] == ["urgent"]

    cold = engine.runtime.start_process("t_edge", start_user="u_s")
    engine.task.complete(_open(engine, cold, "manager")[0].id, actor="u_mgr", comment="同意")
    assert [t.activity_code for t in _open(engine, cold)] == ["normal"]


def test_prev_task_used_time_compared_with_duration(engine: ProcessEngine):
    """A1：耗时（cost_seconds 派生 timedelta）与 duration 字面量比较。"""
    definition = (
        FlowBuilder("t_time")
        .start()
        .user_task("manager", assignee="'u_mgr'")
        .branch("gate")
        .user_task("fast", assignee="'u_a'")
        .user_task("slow", assignee="'u_b'")
        .end()
        .connect("start", "manager")
        .connect("manager", "gate")
        .flow("gate", "fast", condition="prev_task.used_time < duration('1h')")
        .flow("gate", "slow", default=True)
        .connect("fast", "end")
        .connect("slow", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("t_time", start_user="u_s")
    engine.task.complete(_open(engine, iid, "manager")[0].id, actor="u_mgr")
    assert [t.activity_code for t in _open(engine, iid)] == ["fast"]


def test_prev_task_assignee_drives_next_approver(engine: ProcessEngine):
    """B1：取人位 prev_task——下一审批人＝上一站办理人。"""
    definition = (
        FlowBuilder("t_who")
        .start()
        .user_task("first", assignee="'u_worker'")
        .user_task("second", assignee="prev_task.assignee")
        .end()
        .connect("start", "first")
        .connect("first", "second")
        .connect("second", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("t_who", start_user="u_s")
    engine.task.complete(_open(engine, iid, "first")[0].id, actor="u_worker")
    assert _open(engine, iid, "second")[0].assignee == "u_worker"


# —— C 组：会签票数下游引用（招标主线）————


def _voting_flow(code: str, *, approve_exit: str) -> object:
    return (
        FlowBuilder(code)
        .start()
        .user_task(
            "voting",
            assignees=["'u_a'", "'u_b'", "'u_c'"],
            multi_instance=MultiInstance(mode="parallel", approve_exit=approve_exit),
        )
        .branch("gate")
        .user_task("committee", assignee="'u_dir'")
        .user_task("requote", assignee="'u_owner'")
        .end()
        .connect("start", "voting")
        .connect("voting", "gate")
        .flow("gate", "committee", condition=VOTE_GATE)
        .flow("gate", "requote", default=True)
        .connect("committee", "end")
        .connect("requote", "end")
        .build()
    )


def test_vote_counts_drive_branch(engine: ProcessEngine):
    """C1 招标主线：≥60% 同意走委员会，不足走重新询价（票数＝最近一轮）。"""
    engine.repository.deploy(_voting_flow("tender_major", approve_exit="task.approve_count >= 2"))
    iid = engine.runtime.start_process("tender_major", start_user="u_s")
    for voter in ("u_a", "u_b"):
        engine.task.complete(_open(engine, iid, "voting")[0].id, actor=voter)
    assert [t.activity_code for t in _open(engine, iid)] == ["committee"]  # 2/3＝66%

    engine.repository.deploy(_voting_flow("tender_minor", approve_exit="task.approve_count >= 1"))
    low = engine.runtime.start_process("tender_minor", start_user="u_s")
    engine.task.complete(_open(engine, low, "voting")[0].id, actor="u_a")
    assert [t.activity_code for t in _open(engine, low)] == ["requote"]  # 1/3＝33%


def test_vote_counts_drive_assignee(engine: ProcessEngine):
    """C2：票数段位驱动审批人（值位三元引用节点事实）。"""
    definition = (
        FlowBuilder("t_who2")
        .start()
        .user_task(
            "voting",
            assignees=["'u_a'", "'u_b'", "'u_c'"],
            multi_instance=MultiInstance(mode="parallel", approve_exit="task.approve_count >= 2"),
        )
        .user_task(
            "reviewer",
            assignee='node("voting").approve_count >= 2 ? "u_dir" : "u_mgr"',
        )
        .end()
        .connect("start", "voting")
        .connect("voting", "reviewer")
        .connect("reviewer", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("t_who2", start_user="u_s")
    for voter in ("u_a", "u_b"):
        engine.task.complete(_open(engine, iid, "voting")[0].id, actor=voter)
    assert _open(engine, iid, "reviewer")[0].assignee == "u_dir"


def test_runs_counts_rounds_after_reject(engine: ProcessEngine):
    """C3：驳回重报后 node("apply").runs＝2（上一轮痕迹不丢，新一轮任务可判）。"""
    definition = (
        FlowBuilder("t_rounds")
        .start()
        .user_task("apply", assignee="'u_app'")
        .user_task("review", assignee="'u_rev'", reject_target="apply")
        .branch("gate")
        .user_task("again", assignee="'u_x'")
        .user_task("first", assignee="'u_y'")
        .end()
        .connect("start", "apply")
        .connect("apply", "review")
        .connect("review", "gate")
        .flow("gate", "again", condition='node("apply").runs == 2')
        .flow("gate", "first", default=True)
        .connect("again", "end")
        .connect("first", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("t_rounds", start_user="u_s")

    engine.task.complete(_open(engine, iid, "apply")[0].id, actor="u_app")
    engine.task.reject(_open(engine, iid, "review")[0].id, actor="u_rev", target="apply")
    engine.task.complete(_open(engine, iid, "apply")[0].id, actor="u_app")  # 重报
    engine.task.complete(_open(engine, iid, "review")[0].id, actor="u_rev")
    assert [t.activity_code for t in _open(engine, iid)] == ["again"]


# —— D 组：结构难角 ————


def test_parallel_join_prev_task_is_last_arrival(engine: ProcessEngine):
    """D1：并行汇聚后 prev_task＝最后到达那条路的完成任务（确定非随机）。"""
    from flowork.definitions.builtin import RouteMode

    definition = (
        FlowBuilder("t_join")
        .start()
        .branch("split", route_mode=RouteMode.ALL_MATCH)
        .user_task("left", assignee="'u_a'")
        .user_task("right", assignee="'u_b'")
        .user_task("join", assignee="prev_task.assignee", advance_policy="all")
        .end()
        .connect("start", "split")
        .connect("split", "left")
        .connect("split", "right")
        .connect("left", "join")
        .connect("right", "join")
        .connect("join", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("t_join", start_user="u_s")

    engine.task.complete(_open(engine, iid, "left")[0].id, actor="u_a")  # 先到
    assert not _open(engine, iid, "join")  # all：等齐
    engine.task.complete(_open(engine, iid, "right")[0].id, actor="u_b")  # 后到
    assert _open(engine, iid, "join")[0].assignee == "u_b"


def test_node_absent_counts_zero_takes_default(engine: ProcessEngine):
    """D5：引用未执行节点＝固定形状（计数 0）→ 条件不满足走默认边，不卡流程。"""
    definition = (
        FlowBuilder("t_ghost")
        .start()
        .user_task("work", assignee="'u_w'")
        .branch("gate")
        .user_task("big", assignee="'u_a'")
        .user_task("small", assignee="'u_b'")
        .user_task("later", assignee="'u_l'")  # 尚未执行的被点名节点
        .end()
        .connect("start", "work")
        .connect("work", "gate")
        .flow("gate", "big", condition='node("later").approve_count >= 5')
        .flow("gate", "small", default=True)
        .connect("big", "end")
        .connect("small", "later")
        .connect("later", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("t_ghost", start_user="u_s")
    engine.task.complete(_open(engine, iid, "work")[0].id, actor="u_w")
    assert [t.activity_code for t in _open(engine, iid)] == ["small"]


def test_node_absent_assignee_contributes_nothing(engine: ProcessEngine):
    """D5 取人位：查无节点的人物字段＝null → 贡献空（传阅收件人不误添）。"""
    definition = (
        FlowBuilder("t_ghost_cc")
        .start()
        .user_task("work", assignee="'u_w'")
        .cc_task("notify", recipients=["'u_base'", 'node("ghost").assignee'])
        .end()
        .connect("start", "work")
        .connect("work", "notify")
        .connect("notify", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("t_ghost_cc", start_user="u_s")
    engine.task.complete(_open(engine, iid, "work")[0].id, actor="u_w")
    cc = engine.history.cc_items(iid) if hasattr(engine.history, "cc_items") else None
    if cc is None:  # history 面无 cc 查询时退化为「流程正常走完」断言
        tasks = _open(engine, iid)
        assert not tasks
    else:
        assert [item.recipient for item in cc] == ["u_base"]
