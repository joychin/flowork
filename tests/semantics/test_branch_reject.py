"""M2 语义测试：条件分支（命中即走/命中的都走）、同时进行与等齐、公式沙箱、串行会签、
驳回回退、死循环。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.builtin import RouteMode
from flowork.definitions.definition import ProcessVariable
from flowork.definitions.node import MultiInstance
from flowork.engine.engine import ProcessEngine
from flowork.engine.kernel import DeadLoopError, RouteError
from flowork.expr.cel import FormulaError, evaluate_formula


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


_OPS = {"eq": "==", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}


def cond(left: str, op: str, right: object) -> str:
    """便捷：单条件 → CEL 公式（条件单轨化）。"""
    lit = (
        "true"
        if right is True
        else "false"
        if right is False
        else "null"
        if right is None
        else f'"{right}"'
        if isinstance(right, str)
        else str(right)
    )
    return f"{left} {_OPS[op]} {lit}"


def publish(engine: ProcessEngine, definition) -> None:
    engine.repository.deploy(definition)


def complete_all(engine: ProcessEngine, instance_id: str, actors: dict[str, str]) -> None:
    """按 activity→actor 映射依次办完所有待办。"""

    def actor_of(activity: str, fallback: str) -> str:
        return actors.get(activity, fallback)

    for _ in range(20):
        tasks = engine.task.list_tasks(instance_id=instance_id)
        if not tasks:
            return
        task = tasks[0]
        engine.task.complete(task.id, actor=actor_of(task.activity_code, task.assignee))
    raise AssertionError("待办办不完，疑似死循环")


def complete_of(engine: ProcessEngine, instance_id: str, assignee: str) -> None:
    """办理指定人的第一件待办。"""
    task = engine.task.list_tasks(instance_id=instance_id, assignee=assignee)[0]
    engine.task.complete(task.id, actor=assignee)


# —— 条件分支（命中即走）——


def test_first_match_condition_branch(engine: ProcessEngine):
    """条件命中走条件边，未命中走默认边。"""
    definition = (
        FlowBuilder("ex")
        .start()
        .user_task("apply", assignee="'u_user'")
        .branch("route")
        .user_task("high", assignee="'u_boss'")
        .user_task("low", assignee="'u_clerk'")
        .end()
        .connect("start", "apply")
        .connect("apply", "route")
        .flow("route", "high", condition=cond("amount", "gt", 100))
        .flow("route", "low", default=True)
        .connect("high", "end")
        .connect("low", "end")
        .build()
        .model_copy(update={"variables": [ProcessVariable(name="amount", type="number")]})
    )
    publish(engine, definition)

    high = engine.runtime.start_process("ex", start_user="u_user", variables={"amount": 200})
    complete_all(engine, high, {"apply": "u_user", "high": "u_boss"})
    assert engine.runtime.get_instance(high).state == "completed"
    assert any(t.activity_code == "high" for t in engine.history.tokens(high))

    low = engine.runtime.start_process("ex", start_user="u_user", variables={"amount": 50})
    complete_all(engine, low, {"apply": "u_user", "low": "u_clerk"})
    assert any(t.activity_code == "low" for t in engine.history.tokens(low))


def test_first_match_no_match_no_default_raises(engine: ProcessEngine):
    definition = (
        FlowBuilder("ex2")
        .start()
        .branch("route")
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "route")
        .flow("route", "a", condition=cond("x", "eq", 1))
        .flow("route", "b", condition=cond("x", "eq", 2))
        .connect("a", "end")
        .connect("b", "end")
        .build()
        .model_copy(update={"variables": [ProcessVariable(name="x", type="number")]})
    )
    publish(engine, definition)
    with pytest.raises(RouteError):
        engine.runtime.start_process("ex2", start_user="u_user", variables={"x": 99})


# —— 同时进行 + 等全部到齐 ——


def test_parallel_split_and_join(engine: ProcessEngine):
    """同时进行：多条无条件出边即并行；开始条件=all 等两条都完成才继续。"""
    definition = (
        FlowBuilder("par")
        .start()
        .user_task("hr", assignee="'u_hr'")
        .user_task("it", assignee="'u_it'")
        .user_task("final", assignee="'u_boss'", advance_policy="all")
        .end()
        .connect("start", "hr")
        .connect("start", "it")
        .connect("hr", "final")
        .connect("it", "final")
        .connect("final", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("par", start_user="u_user")

    tasks = {t.activity_code: t for t in engine.task.list_tasks(instance_id=instance_id)}
    assert set(tasks) == {"hr", "it"}  # 同时进行（无需分流节点）

    engine.task.complete(tasks["hr"].id, actor="u_hr")
    remaining = [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)]
    assert remaining == ["it"]  # 未到齐
    assert engine.runtime.get_instance(instance_id).state == "running"
    final_tokens = [t for t in engine.history.tokens(instance_id) if t.activity_code == "final"]
    assert final_tokens == []  # 推进门拦截：未建令牌

    it_task = engine.task.list_tasks(instance_id=instance_id, assignee="u_it")[0]
    result = engine.task.complete(it_task.id, actor="u_it")
    assert [t.activity_code for t in result.next_tasks] == ["final"]  # 到齐放行

    complete_all(engine, instance_id, {"final": "u_boss"})
    assert engine.runtime.get_instance(instance_id).state == "completed"
    assert len([t for t in engine.history.tokens(instance_id) if t.activity_code == "final"]) == 1


# —— 命中的都走 + 任一到达即开始（竞速取消兄弟）——


def test_all_match_race_cancels_sibling_branch(engine: ProcessEngine):
    """命中的都走：双臂同时激活；final 任一到达即开始并取消仍在办的兄弟分支。"""
    definition = (
        FlowBuilder("inc")
        .start()
        .user_task("apply", assignee="'u_user'")
        .branch("fork", route_mode=RouteMode.ALL_MATCH)
        .user_task("fast", assignee="'u_fast'")
        .user_task("slow", assignee="'u_slow'")
        .user_task("final", assignee="'u_boss'", advance_policy="any")
        .end()
        .connect("start", "apply")
        .connect("apply", "fork")
        .flow("fork", "fast", condition=cond("x", "ge", 1))
        .flow("fork", "slow", condition=cond("x", "ge", 1))
        .flow("fork", "end", default=True)
        .connect("fast", "final")
        .connect("slow", "final")
        .connect("final", "end")
        .build()
        .model_copy(update={"variables": [ProcessVariable(name="x", type="number")]})
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("inc", start_user="u_user", variables={"x": 1})
    complete_of(engine, instance_id, "u_user")
    # 两个条件同时成立 → 双臂同时待办
    active = {t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)}
    assert active == {"fast", "slow"}

    result = engine.task.complete(
        engine.task.list_tasks(instance_id=instance_id, assignee="u_fast")[0].id, actor="u_fast"
    )
    # final 任一到达即开始；仍在办的 slow 兄弟分支被取消
    assert [t.activity_code for t in result.next_tasks] == ["final"]
    slow_token = next(t for t in engine.history.tokens(instance_id) if t.activity_code == "slow")
    assert slow_token.state == "cancelled"
    assert engine.task.list_tasks(instance_id=instance_id, assignee="u_slow") == []

    complete_all(engine, instance_id, {"final": "u_boss"})
    assert engine.runtime.get_instance(instance_id).state == "completed"


# —— 公式（CEL，kernel-v2 §4.3）——


@pytest.mark.parametrize(
    ("formula", "variables", "expected"),
    [
        ("amount > 1000", {"amount": 2000}, True),
        ("amount > 1000", {"amount": 500}, False),
        ('amount >= 100 && level == "L2"', {"amount": 100, "level": "L2"}, True),
        ("a in [1, 2, 3]", {"a": 2}, True),
        ("items.size() > 1", {"items": [1, 2]}, True),
    ],
)
def test_formula_sandbox(formula, variables, expected):
    assert evaluate_formula(formula, variables) is expected


def test_formula_sandbox_rejects_dangerous():
    with pytest.raises(FormulaError):
        evaluate_formula("__import__('os').system('id')", {})  # CEL 无此语法 → 解析期拒绝
    with pytest.raises(FormulaError):
        evaluate_formula("no_such_fn(1) == 2", {})  # 未注册函数 → 求值期拒绝
    # 非布尔结果按 P0-1 决策 bool(...) 收敛（不再报错），见 tests/expr/test_cel.py


def test_formula_via_branch(engine: ProcessEngine):
    definition = (
        FlowBuilder("fm")
        .start()
        .branch("route")
        .user_task("big", assignee="'u_boss'")
        .user_task("small", assignee="'u_clerk'")
        .end()
        .connect("start", "route")
        .flow("route", "big", condition="amount > 5000 && urgent == true")
        .flow("route", "small", default=True)
        .connect("big", "end")
        .connect("small", "end")
        .build()
        .model_copy(
            update={
                "variables": [
                    ProcessVariable(name="amount", type="number"),
                    ProcessVariable(name="urgent", type="boolean"),
                ]
            }
        )
    )
    publish(engine, definition)
    variables = {"amount": 9000, "urgent": True}
    iid = engine.runtime.start_process("fm", start_user="u_user", variables=variables)
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=iid)] == ["big"]


def test_condition_error_wrapped(engine: ProcessEngine):
    """公式引用未提供变量 → 技术故障：命令失败回滚（实例行不落库）+ 故障单（单轨语义，spec §8）。"""
    from flowork.engine.kernel import EngineExecutionError
    from flowork.errors import InstanceNotFoundError

    definition = (
        FlowBuilder("cerr")
        .start()
        .branch("route")
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "route")
        .flow("route", "a", condition=cond("ghost", "eq", 1))
        .connect("a", "end")
        .build()
        .model_copy(update={"variables": [ProcessVariable(name="ghost", type="number")]})
    )
    publish(engine, definition)
    with pytest.raises(EngineExecutionError, match="公式求值失败"):
        engine.runtime.start_process("cerr", start_user="u_user", variables={})
    incidents = engine.runtime.list_incidents()
    assert incidents and incidents[0]["state"] == "open"  # 独立事务记录的故障单
    with pytest.raises(InstanceNotFoundError):
        engine.runtime.get_instance(incidents[0]["instance_id"])  # 启动命令已回滚


# —— 串行会签 ——


def test_sequential_countersign_one_by_one(engine: ProcessEngine):
    """串行会签：逐人生成待办，all 出口需全员。"""
    definition = (
        FlowBuilder("seq")
        .start()
        .user_task(
            "audit",
            assignees=["'u_1'", "'u_2'", "'u_3'"],
            multi_instance=MultiInstance(
                mode="sequential", approve_exit="task.approve_count >= task.item_count"
            ),
        )
        .end()
        .connect("start", "audit")
        .connect("audit", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("seq", start_user="u_user")

    expected_order = ["u_1", "u_2", "u_3"]
    for index, assignee in enumerate(expected_order):
        tasks = engine.task.list_tasks(instance_id=instance_id)
        assert len(tasks) == 1 and tasks[0].assignee == assignee
        result = engine.task.complete(tasks[0].id, actor=assignee)
        if index < 2:
            assert result.instance_state == "running"
        else:
            assert result.instance_state == "completed"
    assert engine.task.list_tasks(instance_id=instance_id) == []


# —— 驳回 ——


def test_reject_back_then_original_handler_gets_task_again(engine: ProcessEngine):
    """驳回回退：目标节点原办理人重新获得待办；历史只追加。"""
    definition = (
        FlowBuilder("rj")
        .start()
        .user_task("step1", assignee="'u_maker'")
        .user_task("step2", assignee="'u_checker'")
        .user_task("step3", assignee="'u_boss'")
        .end()
        .connect("start", "step1")
        .connect("step1", "step2")
        .connect("step2", "step3")
        .connect("step3", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("rj", start_user="u_maker")
    engine.task.complete(engine.task.list_tasks(instance_id=instance_id)[0].id, actor="u_maker")

    checker_task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.reject(checker_task.id, actor="u_checker", comment="材料不全")

    # step1 原办理人（u_maker）重新待办
    tasks = engine.task.list_tasks(instance_id=instance_id)
    assert [(t.activity_code, t.assignee) for t in tasks] == [("step1", "u_maker")]

    # 历史追加：step2 reject + 复活新任务（不删旧记录）
    history = engine.history.task_history(instance_id)
    outcomes = [(h.activity_code, h.outcome) for h in history]
    assert ("step2", "reject") in outcomes
    assert outcomes.count(("step1", "agree")) == 1  # 原办理记录仍在

    # 重新提交可走完全程
    engine.task.complete(tasks[0].id, actor="u_maker", comment="已补充材料")
    complete_all(engine, instance_id, {"step2": "u_checker", "step3": "u_boss"})
    assert engine.runtime.get_instance(instance_id).state == "completed"
    # 令牌链：step1 出现两次（reject_flag=True 的新令牌）
    step1_tokens = [t for t in engine.history.tokens(instance_id) if t.activity_code == "step1"]
    assert len(step1_tokens) == 2
    assert step1_tokens[1].reject_flag is True


def test_reject_to_explicit_target(engine: ProcessEngine):
    definition = (
        FlowBuilder("rj2")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .user_task("c", assignee="'u_c'", reject_target="a")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "c")
        .connect("c", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("rj2", start_user="u_starter")
    complete_of(engine, instance_id, "u_a")
    complete_of(engine, instance_id, "u_b")
    c_task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.reject(c_task.id, actor="u_c")  # 显式驳回到 a（配置 reject_target）
    tasks = engine.task.list_tasks(instance_id=instance_id)
    assert [(t.activity_code, t.assignee) for t in tasks] == [("a", "u_a")]


def test_countersign_reject_by_one_vote(engine: ProcessEngine):
    """多实例默认 disapprove_exit='>=1'：一票否决即回退，其余待办撤销。"""
    definition = (
        FlowBuilder("rj3")
        .start()
        .user_task("first", assignee="'u_maker'")
        .user_task(
            "panel",
            assignees=["'u_p1'", "'u_p2'"],
            multi_instance=MultiInstance(
                mode="parallel",
                approve_exit="task.approve_count >= task.item_count",
                disapprove_exit="task.disapprove_count >= 1",
            ),
            reject_target="first",
        )
        .end()
        .connect("start", "first")
        .connect("first", "panel")
        .connect("panel", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("rj3", start_user="u_maker")
    complete_of(engine, instance_id, "u_maker")

    p1 = engine.task.list_tasks(instance_id=instance_id, assignee="u_p1")[0]
    engine.task.reject(p1.id, actor="u_p1", comment="否决")

    assert engine.task.list_tasks(instance_id=instance_id) == [] or [
        t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)
    ] == ["first"]
    history_outcomes = [
        (h.activity_code, h.outcome) for h in engine.history.task_history(instance_id)
    ]
    assert ("panel", "auto_pass") in history_outcomes  # p2 的待办被撤销
    assert engine.runtime.get_instance(instance_id).state == "running"


# —— 死循环守卫 ——


def test_dead_loop_guard(engine: ProcessEngine):
    """分支回边死循环：节点完成次数达上限后触发守卫。"""
    definition = (
        FlowBuilder("loop")
        .start()
        .user_task(
            "again",
            assignee="'u_loop'",
        )
        .branch("route")
        .end()
        .connect("start", "again")
        .connect("again", "route")
        .flow("route", "again", condition=cond("go", "eq", True))
        .flow("route", "end", default=True)
        .build()
        .model_copy(update={"variables": [ProcessVariable(name="go", type="boolean")]})
    )
    publish(
        engine,
        definition,
    )
    instance_id = engine.runtime.start_process("loop", start_user="u_user", variables={"go": True})
    with pytest.raises(DeadLoopError):
        for _ in range(60):
            tasks = engine.task.list_tasks(instance_id=instance_id)
            if not tasks:
                break
            engine.task.complete(tasks[0].id, actor="u_loop")
