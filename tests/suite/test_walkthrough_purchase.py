"""逐节点走查·采购场景（静态目录世界）：并行分叉 → 认领/子流程双支路 →
all 等齐 → 比例会签 → 管理员兜底 → 打款完结。"""

from __future__ import annotations

from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers, covers_node
from tests.suite.walkthrough import (
    complete_of,
    entered_activities,
    expect_position,
    reject_of,
)


def start_purchase(engine: ProcessEngine, budget: float, risk: str, **overrides: object) -> str:
    payload: dict[str, object] = {"budget": budget, "risk": risk, "supplier": "华铝"}
    payload.update(overrides)
    return engine.runtime.start_process("suite_purchase", start_user="u_buyer", variables=payload)


def child_instance_of(engine: ProcessEngine, parent_id: str) -> str:
    children = [row.id for row in engine.history.instances() if row.parent_id == parent_id]
    assert children, "call_activity 应生成子实例"
    return children[0]


def drive_final_sign_and_gate(engine: ProcessEngine, iid: str) -> None:
    """收口两段：比例会签（2/2）→ 管理员兜底 → 打款完结。"""
    expect_position(
        engine,
        iid,
        nodes={"final_sign"},
        tasks={("final_sign", "u_cfo"), ("final_sign", "u_compliance")},
        note="all 等齐后会签收口",
    )
    complete_of(engine, iid, "u_cfo")
    expect_position(
        engine,
        iid,
        nodes={"final_sign"},
        tasks={("final_sign", "u_compliance")},
        note=">=60%（2 人取 2）未达：剩一人",
    )
    complete_of(engine, iid, "u_compliance")
    expect_position(
        engine,
        iid,
        nodes={"admin_gate"},
        tasks={("admin_gate", "u_ceo")},
        note="空角色解析走 to_admins 兜底",
    )
    complete_of(engine, iid, "u_ceo")
    expect_position(engine, iid, nodes=set(), state="completed")
    entered = entered_activities(engine, iid)
    for passed in ("pay_order", "purchase_end"):
        assert passed in entered


@covers(
    "route.all_match",
    "route.parallel_fork",
    "join.all_gate",
    "node.call_activity",
    "countersign.approve_ratio",
    "participant.assignee_literal",
    "participant.no_participant_to_admins",
)
@covers_node(
    "suite_purchase",
    "start",
    "dept_review",
    "fork_route",
    "legal_track",
    "compliance_track",
    "final_sign",
    "admin_gate",
    "pay_order",
    "purchase_end",
)
@covers_node("suite_supplier_check", "start", "check", "supplier_end")
def test_full_parallel_lanes_walkthrough(static_engine: ProcessEngine) -> None:
    """大额+高风险：法务直派道与合规子流程道并行，all 等齐后收口。"""
    engine = static_engine
    iid = start_purchase(engine, budget=50000, risk="high")

    expect_position(
        engine,
        iid,
        nodes={"dept_review"},
        tasks={("dept_review", "u_purch_head")},
        note="发起人主管审批",
    )
    complete_of(engine, iid, "u_purch_head")
    expect_position(
        engine,
        iid,
        nodes={"legal_track", "compliance_track"},
        tasks={("legal_track", "u_ceo")},
        note="两条条件边同时命中并行",
    )

    child_id = child_instance_of(engine, iid)
    expect_position(
        engine,
        child_id,
        nodes={"check"},
        tasks={("check", "u_compliance")},
        note="子流程实例落在合规核查",
    )
    complete_of(engine, child_id, "u_compliance", variables={"compliance": "passed"})
    expect_position(
        engine,
        child_id,
        nodes=set(),
        state="completed",
        note="子流程完结",
    )
    expect_position(
        engine,
        iid,
        nodes={"legal_track"},
        tasks={("legal_track", "u_ceo")},
        note="子流程道已落定（到达不驻留令牌）：等齐门等法务道",
    )
    complete_of(engine, iid, "u_ceo")

    archive = engine.history.archive_instance(iid)
    assert archive["instance"]["variables"]["compliance"] == "passed", "output_mappings 回传"
    drive_final_sign_and_gate(engine, iid)


@covers("join.all_gate", "route.default_edge")
@covers_node("suite_purchase", "start", "dept_review", "fork_route", "final_sign")
def test_default_lane_walkthrough(static_engine: ProcessEngine) -> None:
    """小额低风险：两条条件边皆不中，默认边直达收口（单令牌过等齐门）。"""
    engine = static_engine
    iid = start_purchase(engine, budget=100, risk="low")
    complete_of(engine, iid, "u_purch_head")
    expect_position(
        engine,
        iid,
        nodes={"final_sign"},
        tasks={("final_sign", "u_cfo"), ("final_sign", "u_compliance")},
        note="默认边单令牌：等齐门即刻放行",
    )
    drive_final_sign_and_gate(engine, iid)


@covers("join.all_gate", "route.formula")
def test_partial_parallel_lane_waits_then_releases(static_engine: ProcessEngine) -> None:
    """仅大额命中：法务道独行，等齐门在子流程道不可达时放行。"""
    engine = static_engine
    iid = start_purchase(engine, budget=50000, risk="low")
    complete_of(engine, iid, "u_purch_head")
    expect_position(
        engine,
        iid,
        nodes={"legal_track"},
        tasks={("legal_track", "u_ceo")},
        note="仅法务道激活：子流程道条件未中从未起跑，等齐门等法务道落定",
    )
    complete_of(engine, iid, "u_ceo")
    drive_final_sign_and_gate(engine, iid)


@covers("reject.to_start", "participant.expression_manager_of")
def test_purchase_reject_to_originator(static_engine: ProcessEngine) -> None:
    """主管驳回：回发起人重提，重提后全链路仍走通。"""
    engine = static_engine
    iid = start_purchase(engine, budget=50000, risk="high")
    reject_of(engine, iid, "u_purch_head")
    expect_position(
        engine,
        iid,
        nodes={"start"},
        tasks={("start", "u_buyer")},
        note="驳回到发起人",
    )
    complete_of(engine, iid, "u_buyer")
    complete_of(engine, iid, "u_purch_head")
    expect_position(
        engine,
        iid,
        nodes={"legal_track", "compliance_track"},
        tasks={("legal_track", "u_ceo")},
    )
