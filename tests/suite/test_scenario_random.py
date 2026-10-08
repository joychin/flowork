"""随机场景（固定种子）：任意审批组合都必须收敛到一致终态。

与 walkthrough 的"逐步断言"互补——这里只断言跨场景不变量：
终态干净（无未完令牌/无待办）、金额以末次提交为准、历史无重复、
子实例存在性与分支条件一一对应。种子固定，失败可复现。
"""

from __future__ import annotations

import random

from flowork.engine.engine import ProcessEngine
from tests.suite.walkthrough import complete_of, reject_of

HIGH = 5000
SEED = 20261008


def assert_completed_clean(engine: ProcessEngine, iid: str, final_amount: float) -> None:
    assert engine.runtime.get_instance(iid).state == "completed", iid
    assert all(token.state in ("finished", "cancelled") for token in engine.history.tokens(iid)), (
        iid
    )
    variables = engine.history.archive_instance(iid)["instance"]["variables"]
    assert variables["paid"] is True, iid
    assert variables["paid_amount"] == final_amount, iid
    task_ids = [row.task_id for row in engine.history.task_history(instance_id=iid)]
    assert len(task_ids) == len(set(task_ids)), f"历史出现重复任务：{iid}"


def run_expense_scenario(engine: ProcessEngine, rng: random.Random) -> tuple[str, int]:
    """单条报销：金额随机定道，35% 概率中途驳回一次（高额道驳回后改走快道）。"""
    amount = rng.choice([rng.randint(100, 4999), rng.randint(5000, 50000)])
    iid = engine.runtime.start_process(
        "suite_expense",
        start_user="u_dev_wu",
        variables={"amount": amount, "expense_type": "office"},
    )
    if rng.random() < 0.35:
        if amount >= HIGH:
            reject_of(engine, iid, "u_chief_acc")
            amount = rng.randint(100, 4999)
            complete_of(engine, iid, "u_dev_wu", variables={"amount": amount})
            complete_of(engine, iid, "u_lead_be")
        else:
            reject_of(engine, iid, "u_lead_be")
            complete_of(engine, iid, "u_dev_wu", variables={"amount": amount})
            complete_of(engine, iid, "u_lead_be")
    elif amount >= HIGH:
        for assignee in ("u_chief_acc", "u_cash", "u_cfo", "u_ceo"):
            complete_of(engine, iid, assignee)
    else:
        complete_of(engine, iid, "u_lead_be")
    return iid, amount


def test_random_expense_scenarios_converge(suite_engine: ProcessEngine) -> None:
    """30 条随机报销：无论金额与驳回组合，终态不变量全部成立。"""
    engine = suite_engine
    rng = random.Random(SEED)
    scenarios = [run_expense_scenario(engine, rng) for _ in range(30)]
    assert len(engine.history.instances(definition_code="suite_expense")) == 30
    for iid, final_amount in scenarios:
        assert_completed_clean(engine, iid, final_amount)


def test_random_purchase_scenarios_join_and_children(static_engine: ProcessEngine) -> None:
    """随机预算/风险组合：子实例存在当且仅当高风险；等齐门在任意组合下正确放行。"""
    engine = static_engine
    rng = random.Random(SEED + 1)
    children_by_risk = {"high": 0, "low": 0}
    for _ in range(16):
        budget = rng.randint(100, 80000)
        risk = rng.choice(["high", "low"])
        iid = engine.runtime.start_process(
            "suite_purchase",
            start_user="u_buyer",
            variables={"budget": budget, "risk": risk, "supplier": "华铝"},
        )
        complete_of(engine, iid, "u_purch_head")

        children = [row.id for row in engine.history.instances() if row.parent_id == iid]
        assert len(children) == (1 if risk == "high" else 0), (budget, risk)
        if risk == "high":
            children_by_risk["high"] += 1
            complete_of(engine, children[0], "u_compliance", variables={"compliance": "passed"})
        if budget > 20000:
            complete_of(engine, iid, "u_ceo")
        for assignee in ("u_cfo", "u_compliance"):
            complete_of(engine, iid, assignee)
        complete_of(engine, iid, "u_ceo")  # admin_gate 兜底位
        assert engine.runtime.get_instance(iid).state == "completed"
    assert children_by_risk["high"] > 0, "种子组合应覆盖到高风险路径"


def test_random_mixed_world_interleaved(suite_engine: ProcessEngine) -> None:
    """单引擎交错混跑三个定义：完结实例终态干净，在办实例结构自洽。"""
    engine = suite_engine
    rng = random.Random(SEED + 2)

    def start_expense_quick() -> str:
        return engine.runtime.start_process(
            "suite_expense",
            start_user="u_dev_wu",
            variables={"amount": rng.randint(100, 4999), "expense_type": "office"},
        )

    def start_message() -> str:
        return engine.runtime.start_process(
            "suite_message", start_user="u_buyer", variables={"amount": rng.randint(10, 99)}
        )

    def start_lifecycle() -> str:
        return engine.runtime.start_process("suite_lifecycle", start_user="u_dev_wu")

    starters = [start_expense_quick, start_message, start_lifecycle]
    iids = [starters[rng.randrange(3)]() for _ in range(12)]
    assert len(engine.history.instances()) == 12

    finish, keep = iids[:7], iids[7:]
    for iid in finish:
        code = engine.runtime.get_instance(iid).definition_code
        if code == "suite_expense":
            complete_of(engine, iid, "u_lead_be")
        elif code == "suite_message":
            complete_of(engine, iid, "u_buyer")
        else:
            complete_of(engine, iid, "u_dev_wu")
            complete_of(engine, iid, "u_cto")
        assert engine.runtime.get_instance(iid).state == "completed"
        assert engine.task.list_tasks(instance_id=iid) == []
        assert all(t.state != "unfinished" for t in engine.history.tokens(iid))

    for iid in keep:
        info = engine.runtime.get_instance(iid)
        assert info.state == "running"
        assert any(t.state == "unfinished" for t in engine.history.tokens(iid)), (
            "在办实例必须有活跃令牌"
        )
        assert engine.task.list_tasks(instance_id=iid), "在办实例必须有可办任务"
    assert len(engine.history.instances(state="completed")) == len(finish)
    assert len(engine.history.instances(state="running")) == len(keep)
