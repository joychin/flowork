"""PG 流程走查冒烟：会签分流 / 驳回重审 / 子流程回传（真 PG 上的端到端）。

来源：spec §14 支持矩阵——此前端到端只在 SQLite 上验证过。本文件确认
「同一套语义在 PG 的行锁/事务/OptimisticLock 语义下不做变」。
"""

from __future__ import annotations

import pytest

from flowork.api import FlowBuilder, MultiInstance, ProcessEngine, RouteMode

pytestmark = pytest.mark.needs_postgres


def _deploy_countersign_with_branch(engine: ProcessEngine) -> None:
    """起点 → 会签（满 2 票即过）→ 分支（金额）→ 财务/结束。"""
    flow = (
        FlowBuilder("pg_countersign", "会签分流")
        .start()
        .user_task(
            "review",
            name="会签",
            assignees=["'u_a'", "'u_b'", "'u_c'"],
            multi_instance=MultiInstance(approve_exit="task.approve_count >= 2"),
        )
        .branch("gate", name="金额分流", route_mode=RouteMode.FIRST_MATCH)
        .user_task("finance", name="财务复核", assignee="'u_fin'")
        .end()
        .connect("start", "review")
        .connect("review", "gate")
        .flow("gate", "finance", condition="amount >= 1000")
        .flow("gate", "end", condition="amount < 1000")
        .connect("finance", "end")
        .build()
    )
    engine.repository.deploy(flow)


def test_countersign_and_branch_reach_completion(pg_engine: ProcessEngine) -> None:
    """会签三票中两票 + 条件边命中财务 → 全流程 completed。"""
    _deploy_countersign_with_branch(pg_engine)
    instance_id = pg_engine.runtime.start_process(
        "pg_countersign", start_user="u0", variables={"amount": 5000}
    )

    tasks = pg_engine.task.list_tasks(instance_id=instance_id)
    assert len(tasks) == 3  # 并签：三人同时待办
    pg_engine.task.complete(tasks[0].id, actor="u_a")
    pg_engine.task.complete(tasks[1].id, actor="u_b")

    finance = pg_engine.task.list_tasks(instance_id=instance_id, assignee="u_fin")
    assert len(finance) == 1  # 会签满票后分支命中财务臂
    pg_engine.task.complete(finance[0].id, actor="u_fin")

    assert pg_engine.runtime.get_instance(instance_id).state == "completed"


def test_branch_takes_default_arm_when_condition_false(pg_engine: ProcessEngine) -> None:
    """条件不命中走默认边：小额直达结束（不产生财务任务）。"""
    _deploy_countersign_with_branch(pg_engine)
    instance_id = pg_engine.runtime.start_process(
        "pg_countersign", start_user="u0", variables={"amount": 100}
    )
    tasks = pg_engine.task.list_tasks(instance_id=instance_id)
    pg_engine.task.complete(tasks[0].id, actor="u_a")
    pg_engine.task.complete(tasks[1].id, actor="u_b")

    assert pg_engine.runtime.get_instance(instance_id).state == "completed"
    assert pg_engine.task.list_tasks(instance_id=instance_id, assignee="u_fin") == []


def _deploy_rejectable(engine: ProcessEngine) -> None:
    flow = (
        FlowBuilder("pg_reject", "驳回重审")
        .start()
        .user_task("apply", name="申请", assignee="'u_maker'")
        .user_task("review", name="审批", assignee="'u_boss'", reject_target="apply")
        .end()
        .connect("start", "apply")
        .connect("apply", "review")
        .connect("review", "end")
        .build()
    )
    engine.repository.deploy(flow)


def test_reject_revives_and_resubmit_completes(pg_engine: ProcessEngine) -> None:
    """驳回 → 复活申请节点 → 重报 → 审批通过（历史追加、多轮不膨胀）。"""
    _deploy_rejectable(pg_engine)
    instance_id = pg_engine.runtime.start_process("pg_reject", start_user="u_maker")

    apply_task = pg_engine.task.list_tasks(instance_id=instance_id, assignee="u_maker")[0]
    pg_engine.task.complete(apply_task.id, actor="u_maker")

    review = pg_engine.task.list_tasks(instance_id=instance_id, assignee="u_boss")[0]
    pg_engine.task.reject(review.id, actor="u_boss", comment="材料不全")

    revived = pg_engine.task.list_tasks(instance_id=instance_id, assignee="u_maker")
    assert len(revived) == 1  # 复活：原节点新任务
    pg_engine.task.complete(revived[0].id, actor="u_maker")

    review2 = pg_engine.task.list_tasks(instance_id=instance_id, assignee="u_boss")
    assert len(review2) == 1
    pg_engine.task.complete(review2[0].id, actor="u_boss")
    assert pg_engine.runtime.get_instance(instance_id).state == "completed"

    history = pg_engine.history.task_history(instance_id)
    outcomes = [(h.activity_code, h.outcome) for h in history]
    # 追加式历史：申请两次落 agree、审批一次 reject 一次 agree（多轮不膨胀）
    assert outcomes.count(("apply", "agree")) == 2
    assert outcomes.count(("review", "agree")) == 1
    assert outcomes.count(("review", "reject")) == 1
    assert len(history) == 4


def _deploy_parent_child(engine: ProcessEngine) -> None:
    child = (
        FlowBuilder("pg_child", "子流程")
        .start()
        .user_task("verify", name="复核", assignee="'u_checker'")
        .end()
        .connect("start", "verify")
        .connect("verify", "end")
        .build()
    )
    engine.repository.deploy(child)
    parent = (
        FlowBuilder("pg_parent", "父流程")
        .start()
        .user_task("apply", name="申请", assignee="'u_maker'")
        .call_activity(
            "sub",
            name="复核子流程",
            process_code="pg_child",
            output_mappings={"verdict": "result"},
        )
        .end()
        .connect("start", "apply")
        .connect("apply", "sub")
        .connect("sub", "end")
        .build()
    )
    engine.repository.deploy(parent)


def test_call_activity_blocks_until_child_completes(pg_engine: ProcessEngine) -> None:
    """子流程阻塞父侧；子完结后父收口并把输出映射写回父变量。"""
    _deploy_parent_child(pg_engine)
    parent_id = pg_engine.runtime.start_process("pg_parent", start_user="u_maker")

    apply_task = pg_engine.task.list_tasks(instance_id=parent_id)[0]
    pg_engine.task.complete(apply_task.id, actor="u_maker")
    assert pg_engine.runtime.get_instance(parent_id).state == "running"

    children = [i for i in pg_engine.history.instances() if i.parent_id == parent_id]
    assert len(children) == 1 and children[0].state == "running"

    child_task = pg_engine.task.list_tasks(instance_id=children[0].id)[0]
    pg_engine.task.complete(child_task.id, actor="u_checker", variables={"result": "pass"})

    assert pg_engine.runtime.get_instance(children[0].id).state == "completed"
    assert pg_engine.runtime.get_instance(parent_id).state == "completed"
    assert pg_engine.runtime.variables_of(parent_id)["verdict"] == "pass"
