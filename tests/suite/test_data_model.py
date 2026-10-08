"""能力域②数据契约：input 三边界拒收、变量声明、可检索变量索引、数据驱动路由。"""

from __future__ import annotations

import pytest

from flowork.definitions.contract import InputContractError
from flowork.engine.engine import ProcessEngine
from flowork.persistence.variable_index import instances_by_variable
from tests.suite.features import covers
from tests.suite.walkthrough import complete_of, expect_position


def start_expense(engine: ProcessEngine, **overrides: object) -> str:
    payload: dict[str, object] = {"amount": 1200, "expense_type": "travel"}
    payload.update(overrides)
    return engine.runtime.start_process("suite_expense", start_user="u_dev_wu", variables=payload)


# —— input 契约三边界 ——


@covers("contract.start_boundary")
def test_start_boundary_rejects_bad_payload(suite_engine: ProcessEngine) -> None:
    with pytest.raises(InputContractError, match="边界=start"):
        suite_engine.runtime.start_process(  # 缺 amount
            "suite_expense", start_user="u_dev_wu", variables={"expense_type": "travel"}
        )
    with pytest.raises(InputContractError, match="位置=amount"):
        start_expense(suite_engine, amount="八千")  # 类型不符
    with pytest.raises(InputContractError, match="expense_type"):
        start_expense(suite_engine, amount=100, expense_type="gift")
    assert suite_engine.history.instances() == [], "拒收的发起不落库"


@covers("contract.task_output_boundary")
def test_task_output_boundary_rejects_contract_breaking_update(
    suite_engine: ProcessEngine,
) -> None:
    iid = start_expense(suite_engine, amount=1200)
    with pytest.raises(InputContractError, match="边界=task_output"):
        complete_of(suite_engine, iid, "u_lead_be", variables={"amount": -1})  # minimum 0
    expect_position(
        suite_engine,
        iid,
        nodes={"quick_review"},
        tasks={("quick_review", "u_lead_be")},
        note="拒收后命令回滚，待办仍在",
    )


@covers("contract.node_output_boundary", "fault.business_no_incident")
def test_node_output_boundary_rejects_polluting_message(suite_engine: ProcessEngine) -> None:
    """消息关联写入与人工任务同路径：冲毁契约＝业务失败拒收、不产生故障单。"""
    engine = suite_engine
    iid = engine.runtime.start_process(
        "suite_message", start_user="u_buyer", variables={"amount": 100}
    )
    token = next(t for t in engine.history.tokens(iid) if t.state == "unfinished")
    engine.messages.subscribe(iid, token.id, token.activity_code, "order_paid", "EXP-1")

    with pytest.raises(InputContractError, match="边界=node_output"):
        engine.messages.publish_message("order_paid", "EXP-1", {"amount": "污染"})
    assert engine.runtime.list_incidents(instance_id=iid) == [], "契约违例不是技术故障"

    engine.messages.publish_message("order_paid", "EXP-1", {"paid": True})  # 合法载荷放行
    complete_of(engine, iid, "u_buyer")
    expect_position(engine, iid, nodes=set(), state="completed")


# —— 变量声明与检索 ——


@covers("contract.variable_declarations")
def test_variable_declarations_roundtrip(suite_engine: ProcessEngine) -> None:
    definition = suite_engine.repository.get_definition("suite_expense")
    assert definition is not None
    declared = {v.name: v for v in definition.variables}
    assert set(declared) == {"amount", "expense_type", "urgent", "note"}
    assert declared["amount"].type == "number" and declared["amount"].searchable
    assert declared["expense_type"].searchable
    assert declared["urgent"].type == "boolean" and declared["urgent"].searchable
    assert not declared["note"].searchable


@covers("contract.variable_index_search")
def test_searchable_variable_index_queries(suite_engine: ProcessEngine) -> None:
    low = start_expense(suite_engine, amount=800, expense_type="office")
    mid = start_expense(suite_engine, amount=8000, expense_type="travel")
    high = start_expense(suite_engine, amount=20000, expense_type="training")

    with suite_engine.session_factory() as session:
        assert instances_by_variable(session, "amount", number=800) == [low]
        assert set(instances_by_variable(session, "amount", number_gt=5000)) == {mid, high}
        assert instances_by_variable(session, "expense_type", text="travel") == [mid]
        assert instances_by_variable(session, "note") == []  # 非 searchable 变量不入索引


@covers("route.formula")
def test_data_driven_routing_threshold_flip(suite_engine: ProcessEngine) -> None:
    """同一份数据契约下的路由翻转：阈值两侧走不同支路。"""
    below = start_expense(suite_engine, amount=4999)
    expect_position(
        suite_engine, below, nodes={"quick_review"}, tasks={("quick_review", "u_lead_be")}
    )
    at_threshold = start_expense(suite_engine, amount=5000)  # ge 含边界
    expect_position(
        suite_engine,
        at_threshold,
        nodes={"fin_countersign"},
        tasks={("fin_countersign", "u_chief_acc"), ("fin_countersign", "u_cash")},
    )


@covers("reject.to_start")
def test_resubmit_recomputes_route_from_new_data(suite_engine: ProcessEngine) -> None:
    """驳回改参重提：路由按新数据复算（数据即路由的事实源）。"""
    engine = suite_engine
    iid = start_expense(engine, amount=8000)
    complete_of(engine, iid, "u_chief_acc")
    task = engine.task.list_tasks(instance_id=iid, assignee="u_cash")[0]
    engine.task.reject(task.id, actor="u_cash")

    expect_position(engine, iid, nodes={"start"}, tasks={("start", "u_dev_wu")})
    complete_of(engine, iid, "u_dev_wu", variables={"amount": 8000, "expense_type": "office"})
    expect_position(
        engine,
        iid,
        nodes={"fin_countersign"},
        tasks={("fin_countersign", "u_chief_acc"), ("fin_countersign", "u_cash")},
        note="金额未变仍走高额道（复算不因驳回而漂移）",
    )
