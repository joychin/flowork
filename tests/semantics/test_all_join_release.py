"""all 汇聚放行判定（kernel-v2 spec §2.3 可达性语义，P0-7）测试。

判据：除已停在本节点的令牌外，没有任何**未完令牌**能沿图（结构可达、不评条件）
到达本节点 → 全部入边满足，放行。相对旧「来源 settled 即满足」语义修复两处缺陷：
①条件分支未命中的臂不再永久死锁（包容网关映射成立）；②循环迭代里上一轮的
settled 令牌不再导致提前放行。any 竞速与驳回归零绕门语义不变（回归另见
test_advance_policy.py）。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.builtin import RouteMode
from flowork.definitions.flow import FlowCondition
from flowork.engine.engine import ProcessEngine
from flowork.engine.kernel import DeadLoopError


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def cond(left: str, op: str, right: object) -> FlowCondition:
    return FlowCondition(groups=[[{"left": left, "operator": op, "right": right}]])


def publish(engine: ProcessEngine, definition) -> None:
    engine.repository.save_definition(definition)
    engine.repository.publish(definition.code)


def active_of(engine: ProcessEngine, instance_id: str) -> set[str]:
    return {t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)}


def task_of(engine: ProcessEngine, instance_id: str, assignee: str):
    return engine.task.list_tasks(instance_id=instance_id, assignee=assignee)[0]


def tokens_at(engine: ProcessEngine, instance_id: str, code: str) -> list:
    return [t for t in engine.history.tokens(instance_id) if t.activity_code == code]


def test_unmatched_first_match_arm_releases_join(engine: ProcessEngine):
    """包容分支（命中即走）未选中的臂不阻塞：取中的臂完成即放行 all 汇聚。"""
    definition = (
        FlowBuilder("aj_first")
        .start()
        .branch("route")
        .user_task("high", assignee="u_h")
        .user_task("low", assignee="u_l")
        .user_task("join", assignee="u_j", advance_policy="all")
        .end()
        .connect("start", "route")
        .flow("route", "high", condition=cond("amount", "ge", 100))
        .flow("route", "low", default=True)
        .connect("high", "join")
        .connect("low", "join")
        .connect("join", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process(
        "aj_first", start_user="u_user", variables={"amount": 200}
    )
    assert active_of(engine, instance_id) == {"high"}

    result = engine.task.complete(task_of(engine, instance_id, "u_h").id, actor="u_h")
    # 旧语义此处死锁：low 来源永不到达，join 永不激活
    assert [t.activity_code for t in result.next_tasks] == ["join"]
    assert len(tokens_at(engine, instance_id, "join")) == 1

    engine.task.complete(task_of(engine, instance_id, "u_j").id, actor="u_j")
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_all_match_partial_hit_releases_join(engine: ProcessEngine):
    """包容分支（命中的都走）部分命中：仅命中的臂到达即放行 all 汇聚。"""
    definition = (
        FlowBuilder("aj_partial")
        .start()
        .branch("route", route_mode=RouteMode.ALL_MATCH)
        .user_task("arm_a", assignee="u_a")
        .user_task("arm_b", assignee="u_b")
        .user_task("join", assignee="u_j", advance_policy="all")
        .end()
        .connect("start", "route")
        .flow("route", "arm_a", condition=cond("x", "ge", 1))
        .flow("route", "arm_b", condition=cond("x", "ge", 5))
        .connect("arm_a", "join")
        .connect("arm_b", "join")
        .connect("join", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process(
        "aj_partial", start_user="u_user", variables={"x": 1}
    )
    assert active_of(engine, instance_id) == {"arm_a"}

    result = engine.task.complete(task_of(engine, instance_id, "u_a").id, actor="u_a")
    assert [t.activity_code for t in result.next_tasks] == ["join"]
    engine.task.complete(task_of(engine, instance_id, "u_j").id, actor="u_j")
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_loop_iteration_waits_for_inflight_sibling(engine: ProcessEngine):
    """循环迭代：上一轮 settled 令牌不算数，本轮在途兄弟未完令牌仍阻塞放行。"""
    definition = (
        FlowBuilder("aj_loop")
        .start()
        .user_task("a", assignee="u_a")
        .user_task("b", assignee="u_b")
        .user_task("k", assignee="u_k", advance_policy="all")
        .end()
        .connect("start", "a")
        .connect("start", "b")
        .connect("a", "k")
        .connect("b", "k")
        .flow("k", "a", condition=cond("go", "eq", True))
        .flow("k", "b", condition=cond("go", "eq", True))
        .flow("k", "end", default=True)
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process(
        "aj_loop", start_user="u_user", variables={"go": True}
    )

    # 第 1 轮：a 先到、b 在途 → k 等待；b 到齐 → k 放行一次
    engine.task.complete(task_of(engine, instance_id, "u_a").id, actor="u_a")
    assert active_of(engine, instance_id) == {"b"}
    engine.task.complete(task_of(engine, instance_id, "u_b").id, actor="u_b")
    assert active_of(engine, instance_id) == {"k"}
    assert len(tokens_at(engine, instance_id, "k")) == 1

    # 第 2 轮：k 扇出 a、b；a 先到而 b 在途 → 旧语义因上一轮 settled 提前放行（缺陷）
    engine.task.complete(task_of(engine, instance_id, "u_k").id, actor="u_k")
    assert active_of(engine, instance_id) == {"a", "b"}
    engine.task.complete(task_of(engine, instance_id, "u_a").id, actor="u_a")
    assert active_of(engine, instance_id) == {"b"}  # k 未提前激活
    engine.task.complete(task_of(engine, instance_id, "u_b").id, actor="u_b")
    assert active_of(engine, instance_id) == {"k"}
    assert len(tokens_at(engine, instance_id, "k")) == 2  # 每轮恰好激活一次

    # 收口：k 提交 go=false 走默认边 → 实例完成
    engine.task.complete(
        task_of(engine, instance_id, "u_k").id, actor="u_k", variables={"go": False}
    )
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_reject_reentry_then_unmatched_arm_completes(engine: ProcessEngine):
    """驳回重入 all 汇聚子图：驳回归零绕门后，重提改走未命中臂，汇聚照常放行。"""
    definition = (
        FlowBuilder("aj_reject")
        .start()
        .user_task("fill", assignee="u_f")
        .branch("route")
        .user_task("mgr_h", assignee="u_h", reject_target="fill")
        .user_task("mgr_l", assignee="u_l")
        .user_task("join", assignee="u_j", advance_policy="all")
        .end()
        .connect("start", "fill")
        .connect("fill", "route")
        .flow("route", "mgr_h", condition=cond("lvl", "ge", 2))
        .flow("route", "mgr_l", default=True)
        .connect("mgr_h", "join")
        .connect("mgr_l", "join")
        .connect("join", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("aj_reject", start_user="u_f", variables={"lvl": 2})
    engine.task.complete(task_of(engine, instance_id, "u_f").id, actor="u_f")
    assert active_of(engine, instance_id) == {"mgr_h"}

    # 驳回：不经推进门直达 fill（语义保持）
    engine.task.reject(task_of(engine, instance_id, "u_h").id, actor="u_h", comment="改低级别")
    assert active_of(engine, instance_id) == {"fill"}

    # 重提改走默认臂：mgr_h 本轮未命中，join 放行不因它死等
    engine.task.complete(task_of(engine, instance_id, "u_f").id, actor="u_f", variables={"lvl": 1})
    assert active_of(engine, instance_id) == {"mgr_l"}
    engine.task.complete(task_of(engine, instance_id, "u_l").id, actor="u_l")
    assert active_of(engine, instance_id) == {"join"}
    engine.task.complete(task_of(engine, instance_id, "u_j").id, actor="u_j")
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_dead_loop_guard_regression_on_all_join_cycle(engine: ProcessEngine):
    """死循环守卫回归：all 汇聚环路持续折返仍被 max_node_loops 拦截。"""
    definition = (
        FlowBuilder("aj_guard")
        .start()
        .user_task("a", assignee="u_a")
        .user_task("b", assignee="u_b")
        .user_task("k", assignee="u_k", advance_policy="all")
        .end()
        .connect("start", "a")
        .connect("start", "b")
        .connect("a", "k")
        .connect("b", "k")
        .flow("k", "a", condition=cond("go", "eq", True))
        .flow("k", "b", condition=cond("go", "eq", True))
        .flow("k", "end", default=True)
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process(
        "aj_guard", start_user="u_user", variables={"go": True}
    )
    with pytest.raises(DeadLoopError):
        # 每轮 3 个任务各完成一次，max_node_loops=50 ⇒ ~3×50 次提交内必触guard
        for _ in range(200):
            tasks = engine.task.list_tasks(instance_id=instance_id)
            if not tasks:
                break
            engine.task.complete(tasks[0].id, actor=tasks[0].assignee)
