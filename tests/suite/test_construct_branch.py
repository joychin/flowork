"""分流构造族走查：first_match 三情形 / all_match 三情形 + 混合票 + 路由审计序列断言。

路由断言形状说明（实测钉死，见 sequence_audit 模块注释）：edges 只含条件边、
默认边不进 edges、无条件边落空 edges 行、selected 按激活顺序。
"""

from __future__ import annotations

from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers, covers_node
from tests.suite.invariants import assert_no_dangling
from tests.suite.sequence_audit import route_of, route_rows
from tests.suite.walkthrough import complete_of, reject_of
from tests.suite.world_flows_branch import start_branch


def _running(engine: ProcessEngine, instance_id: str) -> list[str]:
    """当前运行中的节点（history.tokens 是全量含已完结，须过滤）。"""
    return [t.activity_code for t in engine.history.tokens(instance_id) if t.state == "unfinished"]


# —— first_match 三情形 ——


@covers("route.first_match", "route.formula", "query.route_decisions")
@covers_node("suite_branch_matrix", "start", "gate1", "a_gold")
def test_first_match_hits_first_edge(static_engine: ProcessEngine) -> None:
    """两条件同时为真取边序第一（审计可见双 true 与择道）。"""
    iid = start_branch(static_engine, tier=100)
    assert _running(static_engine, iid) == ["a_gold"]
    row = route_of(static_engine, iid, "gate1")
    assert row["mode"] == "first_match"
    assert row["edges"] == [("gate1->a_gold", "true"), ("gate1->a_silver", "true")]
    assert row["selected"] == ["gate1->a_gold"]
    # 无条件边也落审计行（edges 空、mode 恒 all_match）
    start_row = route_of(static_engine, iid, "start")
    assert start_row["edges"] == [] and start_row["selected"] == ["start->gate1"]
    assert start_row["snapshot_keys"] >= {"now", "instance"}


@covers_node("suite_branch_matrix", "a_silver")
def test_first_match_hits_second_edge(static_engine: ProcessEngine) -> None:
    """只命中第二条：前边 false、后边 true。"""
    iid = start_branch(static_engine, tier=60)
    assert _running(static_engine, iid) == ["a_silver"]
    assert route_of(static_engine, iid, "gate1")["edges"] == [
        ("gate1->a_gold", "false"),
        ("gate1->a_silver", "true"),
    ]


@covers("route.default_edge")
@covers_node("suite_branch_matrix", "a_bronze")
def test_first_match_falls_to_default(static_engine: ProcessEngine) -> None:
    """全不命中落默认边：edges=各条件 false，selected=默认边。"""
    iid = start_branch(static_engine, tier=10)
    assert _running(static_engine, iid) == ["a_bronze"]
    row = route_of(static_engine, iid, "gate1")
    assert row["edges"] == [("gate1->a_gold", "false"), ("gate1->a_silver", "false")]
    assert row["selected"] == ["gate1->a_bronze"]


# —— all_match 三情形 ——


@covers("route.all_match")
@covers_node("suite_branch_matrix", "gate2", "b_risk")
def test_all_match_single_arm(static_engine: ProcessEngine) -> None:
    """单条件命中：单臂激活直达收口。"""
    iid = start_branch(static_engine, tier=60, risk="high")
    complete_of(static_engine, iid, "u_cash")
    assert set(_running(static_engine, iid)) == {"b_risk"}
    row = route_of(static_engine, iid, "gate2")
    assert row["edges"] == [("gate2->b_risk", "true"), ("gate2->b_go", "false")]
    assert row["selected"] == ["gate2->b_risk"]
    complete_of(static_engine, iid, "u_compliance")
    assert static_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(static_engine)


@covers("route.parallel_fork", "join.all_gate")
@covers_node("suite_branch_matrix", "b_go")
def test_all_match_both_arms_wait_join(static_engine: ProcessEngine) -> None:
    """双臂激活：done（advance_policy=all）等齐两臂，先到不满不收口。"""
    iid = start_branch(static_engine, tier=60, risk="high", go=True)
    complete_of(static_engine, iid, "u_cash")
    assert set(_running(static_engine, iid)) == {"b_risk", "b_go"}
    assert route_of(static_engine, iid, "gate2")["selected"] == [
        "gate2->b_risk",
        "gate2->b_go",
    ]

    complete_of(static_engine, iid, "u_compliance")
    assert static_engine.runtime.get_instance(iid).state == "running"  # 等另一臂
    complete_of(static_engine, iid, "u_purch_head")
    assert static_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(static_engine)


@covers("route.default_edge")
@covers_node("suite_branch_matrix", "b_none", "done", "branch_end")
def test_all_match_empty_falls_default(static_engine: ProcessEngine) -> None:
    """条件全 false：默认臂独走。"""
    iid = start_branch(static_engine, tier=60)
    complete_of(static_engine, iid, "u_cash")  # 先过 gate1（silver 道），才轮到 gate2
    assert _running(static_engine, iid) == ["b_none"]
    row = route_of(static_engine, iid, "gate2")
    assert row["edges"] == [("gate2->b_risk", "false"), ("gate2->b_go", "false")]
    assert row["selected"] == ["gate2->b_none"]
    complete_of(static_engine, iid, "u_buyer")
    assert static_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(static_engine)


@covers("reject.cancel_sibling_tokens", "reject.explicit_target", "query.tokens")
def test_reject_in_all_match_arm_cancels_sibling(static_engine: ProcessEngine) -> None:
    """混合票：双臂激活后驳回其一 → 兄弟令牌取消、整体回退 start 重报重走。"""
    iid = start_branch(static_engine, tier=100, risk="high", go=True)
    complete_of(static_engine, iid, "u_cfo")
    reject_of(static_engine, iid, "u_purch_head", comment="加急不成立")

    tokens = {t.activity_code: t.state for t in static_engine.history.tokens(iid)}
    assert tokens["b_go"] == "finished"  # 发起驳回的臂：驳回=完成动作，令牌终结
    assert tokens["b_risk"] == "cancelled"  # 兄弟臂未完结 → 取消
    assert tokens["a_gold"] == "finished"  # 上游已正常走完
    running = [
        t.activity_code for t in static_engine.history.tokens(iid) if t.state == "unfinished"
    ]
    assert running == ["start"]  # 回到发起人重报

    # 重报 → 重走双臂 → 完结
    complete_of(static_engine, iid, "u_dev_wu")
    complete_of(static_engine, iid, "u_cfo")
    complete_of(static_engine, iid, "u_compliance")
    complete_of(static_engine, iid, "u_purch_head")
    assert static_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(static_engine)
    # 多轮路由：gate1 出现两次（首走 + 驳回重走），审计按时间正序可回放
    gate1_rows = [r for r in route_rows(static_engine, iid) if r["source"] == "gate1"]
    assert len(gate1_rows) == 2
