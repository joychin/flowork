"""逐节点走查·报销主场景：五条路径打穿 kitchen-sink 定义（每次推进即断言位置）。"""

from __future__ import annotations

from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers, covers_node
from tests.suite.walkthrough import (
    complete_of,
    entered_activities,
    expect_position,
    reject_of,
)


def start_expense(engine: ProcessEngine, **overrides: object) -> str:
    payload: dict[str, object] = {"amount": 1200, "expense_type": "travel"}
    payload.update(overrides)
    return engine.runtime.start_process("suite_expense", start_user="u_dev_wu", variables=payload)


@covers(
    "route.first_match",
    "route.default_edge",
    "participant.expression_manager_of",
    "node.start",
    "node.branch",
    "node.user_task",
    "node.cc_task",
    "node.service_task",
    "node.end",
)
@covers_node(
    "suite_expense", "start", "amount_route", "quick_review", "cc_fin", "pay", "expense_end"
)
def test_quick_lane_walkthrough(suite_engine: ProcessEngine) -> None:
    """小额默认边：主管快审 → 传阅/打款自动走通 → 实例完结。"""
    engine = suite_engine
    iid = start_expense(engine, amount=1200)

    expect_position(
        engine,
        iid,
        nodes={"quick_review"},
        tasks={("quick_review", "u_lead_be")},
        state="running",
        note="发起后应停在主管快审",
    )
    complete_of(engine, iid, "u_lead_be")

    expect_position(engine, iid, nodes=set(), state="completed", note="快审通过即完结")
    entered = entered_activities(engine, iid)
    for passed in ("start", "amount_route", "quick_review", "cc_fin", "pay", "expense_end"):
        assert passed in entered, f"自动节点未走通：{passed}"
    assert engine.task.list_cc_tasks(recipient="u_cfo"), "传阅件应生成"


@covers(
    "route.structured_conditions",
    "countersign.parallel",
    "countersign.sequential",
    "countersign.approve_all",
    "participant.assignees_list",
)
@covers_node("suite_expense", "fin_countersign", "level_sign")
def test_high_lane_walkthrough(suite_engine: ProcessEngine) -> None:
    """高额条件边：财务并签（2/2）→ 逐级串签（CFO→CEO）→ 完结。"""
    engine = suite_engine
    iid = start_expense(engine, amount=8000)

    expect_position(
        engine,
        iid,
        nodes={"fin_countersign"},
        tasks={("fin_countersign", "u_chief_acc"), ("fin_countersign", "u_cash")},
        note="会签两人同时待办",
    )
    complete_of(engine, iid, "u_chief_acc")
    expect_position(
        engine,
        iid,
        nodes={"fin_countersign"},
        tasks={("fin_countersign", "u_cash")},
        note="1/2 未达出口，仅剩一人待办",
    )
    complete_of(engine, iid, "u_cash")

    expect_position(
        engine, iid, nodes={"level_sign"}, tasks={("level_sign", "u_cfo")}, note="串签逐人：先 CFO"
    )
    complete_of(engine, iid, "u_cfo")
    expect_position(
        engine, iid, nodes={"level_sign"}, tasks={("level_sign", "u_ceo")}, note="串签逐人：后 CEO"
    )
    complete_of(engine, iid, "u_ceo")
    expect_position(engine, iid, nodes=set(), state="completed")


@covers("participant.auto_pass_originator")
def test_originator_self_lane_auto_passes(suite_engine: ProcessEngine) -> None:
    """发起人本人即主管：快审自动通过，流程即刻走完。"""
    engine = suite_engine
    iid = engine.runtime.start_process(
        "suite_expense",
        start_user="u_lead_be",
        variables={"amount": 800, "expense_type": "office"},
    )
    expect_position(engine, iid, nodes=set(), state="completed", note="发起人自动通过")
    rows = engine.history.task_history(instance_id=iid, assignee="u_lead_be")
    assert rows and rows[0].outcome == "auto_pass"


@covers("reject.node_target", "reject.to_start", "reject.cause_token")
def test_reject_to_start_then_reroute(suite_engine: ProcessEngine) -> None:
    """会签驳回到发起：改小金额重提，分支按新值复算走快审道。"""
    engine = suite_engine
    iid = start_expense(engine, amount=8000)
    revived = reject_of(engine, iid, "u_chief_acc")

    expect_position(
        engine,
        iid,
        nodes={"start"},
        tasks={("start", "u_dev_wu")},
        note="驳回到 start：发起人重新提交",
    )
    assert revived.next_tasks and revived.next_tasks[0].assignee == "u_dev_wu"
    sibling_rows = [
        row for row in engine.history.task_history(instance_id=iid) if row.assignee == "u_cash"
    ]
    assert any(row.cause_token_id and row.outcome == "auto_pass" for row in sibling_rows), (
        "驳回时剩余会签待办应归档为 auto_pass 并带 cause_token_id 溯源"
    )

    complete_of(engine, iid, "u_dev_wu", variables={"amount": 900})
    expect_position(
        engine,
        iid,
        nodes={"quick_review"},
        tasks={("quick_review", "u_lead_be")},
        note="金额改小后分支复算进快审道",
    )
    complete_of(engine, iid, "u_lead_be")
    expect_position(engine, iid, nodes=set(), state="completed")


@covers("reject.explicit_target")
def test_reject_with_explicit_target(suite_engine: ProcessEngine) -> None:
    """显式 target 覆盖节点配置（会签配的是 start）：一票否决直打 level_sign。"""
    engine = suite_engine
    iid = start_expense(engine, amount=8000)
    engine.task.reject(
        engine.task.list_tasks(instance_id=iid, assignee="u_chief_acc")[0].id,
        actor="u_chief_acc",
        target="level_sign",
    )
    expect_position(
        engine,
        iid,
        nodes={"level_sign"},
        tasks={("level_sign", "u_cfo")},
        note="显式 target 压过节点 reject_target=start",
    )
    complete_of(engine, iid, "u_cfo")
    complete_of(engine, iid, "u_ceo")
    expect_position(engine, iid, nodes=set(), state="completed")


@covers("countersign.disapprove_all_default", "reject.source_fallback")
def test_sequential_sign_full_disapproval_falls_back_to_source(suite_engine: ProcessEngine) -> None:
    """串签未配 disapprove_exit 默认全票否决：两票否决后回退来源节点，原办理人复活。"""
    engine = suite_engine
    iid = start_expense(engine, amount=8000)
    complete_of(engine, iid, "u_chief_acc")
    complete_of(engine, iid, "u_cash")

    reject_of(engine, iid, "u_cfo")
    expect_position(
        engine,
        iid,
        nodes={"level_sign"},
        tasks={("level_sign", "u_ceo")},
        note="全票否决未达（1/2）：串签继续下一位",
    )
    reject_of(engine, iid, "u_ceo")
    expect_position(
        engine,
        iid,
        nodes={"fin_countersign"},
        tasks={("fin_countersign", "u_chief_acc"), ("fin_countersign", "u_cash")},
        note="2/2 否决达成：回退来源节点，原办理人复活",
    )

    complete_of(engine, iid, "u_chief_acc")
    complete_of(engine, iid, "u_cash")
    complete_of(engine, iid, "u_cfo")
    complete_of(engine, iid, "u_ceo")
    expect_position(engine, iid, nodes=set(), state="completed")
