"""编排特性矩阵：会签变体、任务操作集、并行竞速、公式路由、驳回连带取消（ad-hoc 小图）。"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.definitions.node import MultiInstance
from flowork.engine.engine import ProcessEngine
from flowork.errors import TaskNotAvailableError
from tests.suite.features import covers
from tests.suite.walkthrough import complete_of, expect_position, reject_of, task_of


def start_expense(engine: ProcessEngine, amount: float = 8000) -> str:
    return engine.runtime.start_process(
        "suite_expense",
        start_user="u_dev_wu",
        variables={"amount": amount, "expense_type": "travel"},
    )


@covers("countersign.approve_any", "countersign.auto_pass_remaining")
def test_any_sign_first_vote_exits_and_auto_passes_rest() -> None:
    """或签（approve_exit=any）：一票即出口，剩余待办自动通过。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("adhoc_or")
        .start()
        .user_task(
            "vote",
            "或签",
            assignees=["'u_a'", "'u_b'", "'u_c'"],
            multi_instance=MultiInstance(mode="parallel", approve_exit="task.approve_count >= 1"),
        )
        .end()
        .connect("start", "vote")
        .connect("vote", "end")
        .build()
    )
    iid = engine.runtime.start_process("adhoc_or", start_user="u_s")
    expect_position(
        engine,
        iid,
        nodes={"vote"},
        tasks={("vote", "u_a"), ("vote", "u_b"), ("vote", "u_c")},
    )
    complete_of(engine, iid, "u_b")
    expect_position(engine, iid, nodes=set(), state="completed", note="一票即出")
    rest = [
        r
        for r in engine.history.task_history(instance_id=iid)
        if r.activity_code == "vote" and r.assignee != "u_b"
    ]
    assert rest and all(r.outcome == "auto_pass" for r in rest)


@covers(
    "ops.claim",
    "ops.unclaim",
    "ops.transfer",
    "participant.candidate_users",
    "participant.candidate_groups",
)
def test_claim_family_on_candidate_task() -> None:
    """纯候选人节点在无目录引擎生成可认领任务：认领→取消→再认领→转办→办结。

    注：配了目录的引擎见 tests/semantics/test_candidate_claim.py——
    `test_candidate_task_usable_when_org_directory_configured`（候选节点可用）与
    `test_group_candidate_claim_expands_via_directory`（组候选按目录判归属）。
    """
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("adhoc_claim")
        .start()
        .user_task(
            "pick",
            "候选人任务",
            candidate_users=["'u_a'", "'u_b'"],
            candidate_groups=["'role:legal'"],
        )
        .end()
        .connect("start", "pick")
        .connect("pick", "end")
        .build()
    )
    iid = engine.runtime.start_process("adhoc_claim", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert task.assignee == "", "候选任务不落名"

    with pytest.raises(TaskNotAvailableError):
        engine.task.claim(task.id, "u_stranger")
    engine.task.claim(task.id, "u_a")
    assert engine.task.list_tasks(instance_id=iid)[0].assignee == "u_a"

    engine.task.unclaim(task.id, "u_a")
    assert engine.task.list_tasks(instance_id=iid)[0].assignee == ""

    engine.task.claim(task.id, "u_b")
    moved = engine.task.transfer(task.id, "u_c", actor="u_b")
    assert moved.assignee == "u_c"
    engine.task.complete(moved.id, actor="u_c")
    assert engine.runtime.get_instance(iid).state == "completed"


@covers("route.parallel_fork", "join.any_absorb")
def test_unconditional_fork_and_any_join_absorbs_sibling() -> None:
    """无条件多出边并行分叉；any 汇聚先到先得，迟到分支被吸收取消。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("adhoc_race")
        .start()
        .user_task("a", "支路A", assignee="'u_a'")
        .user_task("b", "支路B", assignee="'u_b'")
        .user_task("j", "先到先得", assignee="'u_j'")
        .end()
        .connect("start", "a")
        .connect("start", "b")
        .connect("a", "j")
        .connect("b", "j")
        .connect("j", "end")
        .build()
    )
    iid = engine.runtime.start_process("adhoc_race", start_user="u_s")
    expect_position(
        engine, iid, nodes={"a", "b"}, tasks={("a", "u_a"), ("b", "u_b")}, note="并行分叉"
    )

    complete_of(engine, iid, "u_a")
    expect_position(engine, iid, nodes={"j"}, tasks={("j", "u_j")}, note="any 汇聚：先到激活")
    assert engine.task.list_tasks(instance_id=iid, assignee="u_b") == [], "兄弟分支被吸收"
    tokens_b = [t for t in engine.history.tokens(iid) if t.activity_code == "b"]
    assert tokens_b and tokens_b[0].state == "cancelled"

    complete_of(engine, iid, "u_j")
    expect_position(engine, iid, nodes=set(), state="completed")


@covers("route.formula")
def test_formula_condition_routes_by_expression() -> None:
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("adhoc_formula")
        .start()
        .branch("gate")
        .user_task("big", "大额", assignee="'u_a'")
        .user_task("small", "小额", assignee="'u_b'")
        .end()
        .flow("start", "gate")
        .flow("gate", "big", condition="amount > 100 && urgent")
        .flow("gate", "small", default=True)
        .flow("big", "end")
        .flow("small", "end")
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
    big = engine.runtime.start_process(
        "adhoc_formula", start_user="u_s", variables={"amount": 200, "urgent": True}
    )
    expect_position(engine, big, nodes={"big"}, tasks={("big", "u_a")})

    small = engine.runtime.start_process(
        "adhoc_formula", start_user="u_s", variables={"amount": 50, "urgent": True}
    )
    expect_position(
        engine, small, nodes={"small"}, tasks={("small", "u_b")}, note="公式不中走默认边"
    )


@covers("participant.originator")
def test_originator_expression_resolves_to_start_user() -> None:
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("adhoc_originator")
        .start()
        .user_task("back", "回发起人", assignee="initiator")
        .end()
        .connect("start", "back")
        .connect("back", "end")
        .build()
    )
    iid = engine.runtime.start_process("adhoc_originator", start_user="u_dev_wu")
    expect_position(engine, iid, nodes={"back"}, tasks={("back", "u_dev_wu")})


@covers("reject.cancel_sibling_tokens", "reject.to_start")
def test_reject_cancels_unfinished_sibling_branches() -> None:
    """并行中驳回：本令牌否决出口，其余未完兄弟令牌归零取消。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("adhoc_sibling")
        .start()
        .user_task("a", "支路A", assignee="'u_a'", reject_target="start")
        .user_task("b", "支路B", assignee="'u_b'")
        .user_task("j", "汇聚", assignee="'u_j'")
        .end()
        .connect("start", "a")
        .connect("start", "b")
        .connect("a", "j")
        .connect("b", "j")
        .connect("j", "end")
        .build()
    )
    iid = engine.runtime.start_process("adhoc_sibling", start_user="u_s")
    reject_of(engine, iid, "u_a")

    expect_position(engine, iid, nodes={"start"}, tasks={("start", "u_s")}, note="驳回到 start")
    assert engine.task.list_tasks(instance_id=iid, assignee="u_b") == []
    token_b = next(t for t in engine.history.tokens(iid) if t.activity_code == "b")
    assert token_b.state == "cancelled"


@covers("ops.add_countersigner", "countersign.auto_pass_remaining")
def test_add_countersigner_raises_quorum_and_auto_passes_rest(suite_engine: ProcessEngine) -> None:
    """中途加签扩容 item_count；出口达成后剩余待办自动通过。"""
    engine = suite_engine
    iid = start_expense(engine)
    engine.task.add_countersigner(task_of(engine, iid, "u_cash"), ["u_cfo"], actor="u_chief_acc")
    expect_position(
        engine,
        iid,
        nodes={"fin_countersign"},
        tasks={
            ("fin_countersign", "u_chief_acc"),
            ("fin_countersign", "u_cash"),
            ("fin_countersign", "u_cfo"),
        },
        note="加签后三人同时待办",
    )
    complete_of(engine, iid, "u_chief_acc")
    complete_of(engine, iid, "u_cash")  # 2/3 达 ">=2"
    expect_position(
        engine,
        iid,
        nodes={"level_sign"},
        tasks={("level_sign", "u_cfo")},
        note="出口达成直达下一节点",
    )
    rows = [
        r
        for r in engine.history.task_history(instance_id=iid)
        if r.activity_code == "fin_countersign" and r.assignee == "u_cfo"
    ]
    assert any(r.outcome == "auto_pass" for r in rows), "剩余待办自动通过"


@covers("ops.withdraw")
def test_withdraw_rebuilds_task_at_original_node() -> None:
    """撤回：取消下游已激活令牌，原节点为本人重建待办（单人节点，避开票数缩水语义）。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("adhoc_withdraw")
        .start()
        .user_task("a", "第一环", assignee="'u_a'")
        .user_task("b", "第二环", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    iid = engine.runtime.start_process("adhoc_withdraw", start_user="u_s")
    done_id = task_of(engine, iid, "u_a")
    complete_of(engine, iid, "u_a")
    expect_position(engine, iid, nodes={"b"}, tasks={("b", "u_b")})

    rebuilt = engine.task.withdraw(done_id, actor="u_a", comment="填错单")
    assert rebuilt.activity_code == "a" and rebuilt.assignee == "u_a"
    expect_position(engine, iid, nodes={"a"}, tasks={("a", "u_a")}, note="撤回重建待办")
    token_b = next(t for t in engine.history.tokens(iid) if t.activity_code == "b")
    assert token_b.state == "cancelled", "撤回取消下游令牌"

    with pytest.raises(TaskNotAvailableError):
        engine.task.withdraw(done_id, actor="u_b")  # 非本人不可撤
    complete_of(engine, iid, "u_a")
    expect_position(engine, iid, nodes={"b"}, tasks={("b", "u_b")})
    complete_of(engine, iid, "u_b")
    expect_position(engine, iid, nodes=set(), state="completed")


@covers("ops.assist", "ops.complete_assist")
def test_assist_does_not_advance_flow(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = start_expense(engine)
    engine.task.assist(task_of(engine, iid, "u_cash"), ["u_cfo"], actor="u_cash")

    assist_task = engine.task.list_tasks(instance_id=iid, assignee="u_cfo")[0]
    assert assist_task.task_type == "assist" and assist_task.activity_code == "fin_countersign"
    with pytest.raises(TaskNotAvailableError):
        engine.task.complete(assist_task.id, actor="u_cfo")  # 协办不可走 complete

    engine.task.complete_assist(assist_task.id, actor="u_cfo")
    expect_position(
        engine,
        iid,
        nodes={"fin_countersign"},
        tasks={("fin_countersign", "u_chief_acc"), ("fin_countersign", "u_cash")},
        note="协办完成不推进",
    )
    complete_of(engine, iid, "u_chief_acc")
    complete_of(engine, iid, "u_cash")
    expect_position(engine, iid, nodes={"level_sign"}, tasks={("level_sign", "u_cfo")})
