"""迟到到达吸收（spec §2.3，v2.3 第二十轮·初学者问答审计 D1）。

any（任一到达即开始）的竞速收尾＝取消**结构上仍能到达本节点**的全部在途令牌
（upstream_basin，与 all 放行门同判据），不限直接前驱——两跳以上的长臂同样
收尾，已完结节点不因迟到到达二次激活重跑（修复前：busy 检查只认未完令牌，
长臂令牌躲过「直接前驱集合」的取消名单，节点办完后迟到到达会整个重跑）。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def task_of(engine: ProcessEngine, instance_id: str, assignee: str):
    return engine.task.list_tasks(instance_id=instance_id, assignee=assignee)[0]


def test_long_arm_cancelled_and_no_reactivation(engine: ProcessEngine):
    """两跳长臂菱形：短臂先激活 any 汇合点 → 长臂就地取消，完结后无二次激活。"""
    definition = (
        FlowBuilder("late2")
        .start()
        .user_task("b", assignee="'u_b'")
        .user_task("c1", assignee="'u_c1'")
        .user_task("c2", assignee="'u_c2'")
        .user_task("e", assignee="'u_e'")
        .end()
        .connect("start", "b")
        .connect("start", "c1")
        .connect("b", "e")
        .connect("c1", "c2")
        .connect("c2", "e")
        .connect("e", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("late2", start_user="u_s")
    assert {t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)} == {
        "b",
        "c1",
    }

    # 短臂先到：e 激活（any），长臂 c1 是「结构可达的在途竞速臂」→ 就地取消
    engine.task.complete(task_of(engine, instance_id, "u_b").id, actor="u_b")
    assert {t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)} == {"e"}
    c1_history = [
        row
        for row in engine.history.task_history(instance_id)
        if row.activity_code == "c1" and row.assignee == "u_c1"
    ]
    assert [row.outcome for row in c1_history] == ["cancelled"]  # 竞速收尾留痕

    # e 办完 → 流程走完；长臂已死，无迟到到达、无二次激活（e 恰好跑一轮）
    engine.task.complete(task_of(engine, instance_id, "u_e").id, actor="u_e")
    assert engine.runtime.get_instance(instance_id).state == "completed"
    e_rows = [row for row in engine.history.task_history(instance_id) if row.activity_code == "e"]
    assert len(e_rows) == 1  # 修复前：c2 办完路由回 e 会重激活（e 两轮、待办复现）
    assert engine.task.list_tasks(instance_id=instance_id) == []


def test_loop_reentry_still_reruns(engine: ProcessEngine):
    """回归：多入边 any 节点的条件环回仍可重跑（竞速取消不杀「新一轮」）。

    loop_e 有两条入边（start 与环回 g）——环回到达时无其他在途竞速臂，
    不在办即重激活：这正是环回/驳回复活「重走」语义的载体，不得误伤。
    """
    definition = (
        FlowBuilder("late3")
        .start()
        .user_task("loop_e", assignee="'u_e'")
        .branch("g", "环回闸门")
        .end("done", "走完")
        .connect("start", "loop_e")
        .connect("loop_e", "g")
        .flow("g", "loop_e", condition='node("loop_e").runs < 2')
        .flow("g", "done", default=True)
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("late3", start_user="u_s")

    for _round in (1, 2):
        engine.task.complete(task_of(engine, instance_id, "u_e").id, actor="u_e")
    assert engine.runtime.get_instance(instance_id).state == "completed"
    e_rows = [
        row for row in engine.history.task_history(instance_id) if row.activity_code == "loop_e"
    ]
    assert len(e_rows) == 2  # 环回重跑两轮，第三轮条件不满足走默认边


def test_parallel_sibling_not_reaching_join_survives(engine: ProcessEngine):
    """回归：与本汇聚点无关的并行兄弟分支不被误杀（不在上游盆地内）。"""
    definition = (
        FlowBuilder("late4")
        .start()
        .user_task("b", assignee="'u_b'")
        .user_task("x", assignee="'u_x'")
        .user_task("c1", assignee="'u_c1'")
        .user_task("e", assignee="'u_e'")
        .end()
        .connect("start", "b")
        .connect("start", "x")
        .connect("start", "c1")
        .connect("b", "e")
        .connect("c1", "e")
        .connect("x", "end")
        .connect("e", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("late4", start_user="u_s")

    # b 先到激活 e：竞速臂只有 c1（被取消）；x 到不了 e、不在竞速臂内 → 存活照办
    engine.task.complete(task_of(engine, instance_id, "u_b").id, actor="u_b")
    assert {t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)} == {"e", "x"}

    # x 先办完到达终点：终点亦是 any（x/e 双前驱）——e 是终点竞速的在途直接
    # 前驱 → 收尾取消（与旧语义一致，route_mix 世界依赖此收尾）；实例完成
    engine.task.complete(task_of(engine, instance_id, "u_x").id, actor="u_x")
    assert engine.runtime.get_instance(instance_id).state == "completed"
    e_history = [
        row for row in engine.history.task_history(instance_id) if row.activity_code == "e"
    ]
    assert [row.outcome for row in e_history] == ["cancelled"]
