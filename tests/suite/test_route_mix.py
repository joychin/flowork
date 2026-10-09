"""混用世界②：流程连线规则 × 找人函数 × 环境变量（suite_route_mix）。

命题：连线条件与取人表达式两处都同时混用三类原料——
引擎词汇函数（contrib 或宿主注册）、环境变量（流程变量）、判断面事实
（prev_task / instance / node("code")），并且两者互相咬合：一级闸门选出的臂
决定「谁是上一站」，二级闸门的条件再读到它。

世界形状（两级闸门）：
    start → intake → g_stage1 ─┬→ arm_fact  ─┐
                               ├→ arm_role  ─┼→ g_stage2 ─┬→ arm_by_role ─┐
                               └→ arm_var ──┘             ├→ arm_by_fact ─┼→ sink(all) → end
                                                          └→ arm_var ─────┘      （默认臂直连 end）

四个走查覆盖：择一臂的事实分支 / 实例事实分支 / 变量默认臂 / 双臂并行汇入 all 汇聚。
"""

from __future__ import annotations

from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers, covers_node
from tests.suite.invariants import assert_no_dangling
from tests.suite.sequence_audit import route_of
from tests.suite.walkthrough import complete_of, expect_position, reject_of


def _start(
    engine: ProcessEngine, user: str, *, amount: float, duty: str, key: str | None = None
) -> str:
    return engine.runtime.start_process(
        "suite_route_mix",
        start_user=user,
        business_key=key,
        variables={"amount": amount, "duty_role": duty},
    )


def _intake(engine: ProcessEngine, iid: str) -> None:
    """办完收件节点（其角色 dept_manager 与变量共同决定一级闸门走向）。"""
    complete_of(engine, iid, "u_purch_head")


@covers("route.first_match", "route.mixed_formula", "route.default_edge", "route.fact_gate")
@covers_node("suite_route_mix", "start", "intake", "g_stage1", "arm_fact")
def test_fact_arm_then_default_arm_walks_to_end(suite_engine: ProcessEngine) -> None:
    """臂一（事实+函数+变量三方合一）：prev_task 是部门主管且金额达标 → 上级审批。

    随后二级闸门两条件皆假（无业务键、发起人非主管）→ 落默认臂，
    默认臂的取人仍是函数吃变量（duty_role=ceo → u_ceo），最后直连终点。
    """
    engine = suite_engine
    iid = _start(engine, "u_dev_wu", amount=9000, duty="ceo")
    _intake(engine, iid)

    # 一级：臂一命中（审批人＝manager_of(发起人)）
    expect_position(engine, iid, nodes={"arm_fact"}, tasks={("arm_fact", "u_lead_be")})
    row = route_of(engine, iid, "g_stage1")
    assert row["mode"] == "first_match"
    assert row["edges"] == [("g_stage1->arm_fact", "true"), ("g_stage1->arm_role", "false")]
    assert row["selected"] == ["g_stage1->arm_fact"]
    assert {"prev_task", "instance", "now"} <= row["snapshot_keys"], "事实条件被读取时留痕"

    complete_of(engine, iid, "u_lead_be")
    # 二级：两条件皆假 → 默认臂；默认臂取人来自变量
    expect_position(engine, iid, nodes={"arm_var"}, tasks={("arm_var", "u_ceo")})
    second = route_of(engine, iid, "g_stage2")
    assert second["mode"] == "all_match"
    assert second["edges"] == [
        ("g_stage2->arm_by_role", "false"),
        ("g_stage2->arm_by_fact", "false"),
    ]
    assert second["selected"] == ["g_stage2->arm_var"]

    complete_of(engine, iid, "u_ceo")
    expect_position(engine, iid, nodes=set(), state="completed")
    assert_no_dangling(engine)


@covers("route.first_match", "route.mixed_formula", "route.fact_gate")
@covers_node("suite_route_mix", "arm_role", "arm_by_fact")
def test_instance_fact_arm_routes_and_second_stage_fact_arm_collapses(
    suite_engine: ProcessEngine,
) -> None:
    """臂二（函数列表成员 + 实例事实）：业务键 VIP 走上角色臂，会签两人各得待办。

    二级闸门由实例事实（business_key）与函数（users_with_role(...).size()）命中，
    单臂汇入 all 汇聚节点——单臂时汇聚即刻收口（无需等第二条臂）。
    """
    engine = suite_engine
    iid = _start(engine, "u_dev_wu", amount=100, duty="ceo", key="VIP")
    _intake(engine, iid)

    expect_position(
        engine,
        iid,
        nodes={"arm_role"},
        tasks={("arm_role", "u_chief_acc"), ("arm_role", "u_cash")},
        note="instance.business_key == 'VIP' 命中角色臂",
    )
    first = route_of(engine, iid, "g_stage1")
    assert first["edges"] == [("g_stage1->arm_fact", "false"), ("g_stage1->arm_role", "true")]

    for assignee in ("u_chief_acc", "u_cash"):
        complete_of(engine, iid, assignee)
    expect_position(engine, iid, nodes={"arm_by_fact"}, tasks={("arm_by_fact", "u_lead_be")})
    second = route_of(engine, iid, "g_stage2")
    assert second["edges"] == [
        ("g_stage2->arm_by_role", "false"),
        ("g_stage2->arm_by_fact", "true"),
    ]

    complete_of(engine, iid, "u_lead_be")
    expect_position(engine, iid, nodes=set(), state="completed", note="单臂即刻过 all 汇聚")
    assert_no_dangling(engine)


@covers("route.all_match", "join.all_gate", "route.mixed_formula", "route.fact_gate")
@covers_node("suite_route_mix", "g_stage2", "arm_by_role", "sink")
def test_second_stage_two_arms_parallel_then_all_join(suite_engine: ProcessEngine) -> None:
    """二级双臂并行：两条条件都命中 → 两臂同时开工，等齐才收割口。

    发起人是部门主管 + 金额达标（函数谓词 + 变量）命中角色臂；
    同时携业务键（实例事实）+ 目录有 CEO（函数）命中事实臂。
    先办完一臂不收口（advance_policy=all），第二臂办完才走 sink → end。
    """
    engine = suite_engine
    iid = _start(engine, "u_cto", amount=9000, duty="ceo", key="BK-1")
    _intake(engine, iid)
    complete_of(engine, iid, "u_cto")  # 臂一办理人＝manager_of(u_cto)＝本人（研发中心主管）

    expect_position(
        engine,
        iid,
        nodes={"arm_by_role", "arm_by_fact"},
        tasks={
            ("arm_by_role", "u_chief_acc"),
            ("arm_by_role", "u_cash"),
            ("arm_by_fact", "u_cto"),
        },
        note="两臂并行开工（取人两档不同：函数过滤名单 / 函数吃发起人）",
    )
    second = route_of(engine, iid, "g_stage2")
    assert second["edges"] == [
        ("g_stage2->arm_by_role", "true"),
        ("g_stage2->arm_by_fact", "true"),
    ]
    assert second["selected"] == ["g_stage2->arm_by_role", "g_stage2->arm_by_fact"]
    assert "node" in second["snapshot_keys"], "点名节点事实被读取时留痕"

    complete_of(engine, iid, "u_chief_acc")
    complete_of(engine, iid, "u_cash")
    expect_position(
        engine,
        iid,
        nodes={"arm_by_fact"},
        tasks={("arm_by_fact", "u_cto")},
        state="running",
        note="一臂已到、二臂未到：汇聚不放行",
    )
    complete_of(engine, iid, "u_cto")
    expect_position(engine, iid, nodes=set(), state="completed", note="两臂齐 → 收口 → 终点")
    assert_no_dangling(engine)


@covers("route.default_edge", "route.mixed_formula")
@covers_node("suite_route_mix", "arm_var")
def test_variable_drives_default_arm_and_reroute_switches_arm(
    suite_engine: ProcessEngine,
) -> None:
    """同一条连线，变量换值即换道：默认臂取人由 duty_role 决定；改值后走另一臂。

    这就是「连线规则 × 环境变量」的最小闭环——定义不改、流程照跑，
    改的是运行中的变量（外部改值走 RuntimeService.update_variables）。
    """
    engine = suite_engine
    iid = _start(engine, "u_dev_wu", amount=100, duty="finance_approver")
    _intake(engine, iid)
    expect_position(
        engine,
        iid,
        nodes={"arm_var"},
        tasks={("arm_var", "u_chief_acc"), ("arm_var", "u_cash")},
        note="默认臂取人＝users_with_role(duty_role)",
    )
    complete_of(engine, iid, "u_chief_acc")
    complete_of(engine, iid, "u_cash")
    expect_position(engine, iid, nodes=set(), state="completed")

    # 另一实例：改值把落点从默认臂拨到事实臂（金额与角色双双达标）
    rerouted = _start(engine, "u_lead_be", amount=100, duty="ceo")
    engine.runtime.update_variables(
        rerouted, {"amount": 9000}, actor="u_admin", reason="补录合同金额"
    )
    _intake(engine, rerouted)
    expect_position(
        engine,
        rerouted,
        nodes={"arm_fact"},
        tasks={("arm_fact", "u_lead_be")},
        note="改值后落事实臂（其上级＝后端组主管本人）",
    )
    reroute_row = route_of(engine, rerouted, "g_stage1")
    assert reroute_row["edges"] == [
        ("g_stage1->arm_fact", "true"),
        ("g_stage1->arm_role", "false"),
    ]
    complete_of(engine, rerouted, "u_lead_be")
    complete_of(engine, rerouted, "u_ceo")  # 二级默认臂
    expect_position(engine, rerouted, nodes=set(), state="completed")
    assert_no_dangling(engine)


@covers("reject.to_start", "route.mixed_formula")
@covers_node("suite_route_mix", "route_end")
def test_reject_from_fact_arm_replays_mixed_gate(suite_engine: ProcessEngine) -> None:
    """驳回重走：事实臂驳回回发起人，重报后同一套混用公式重算一次（审计两行）。"""
    engine = suite_engine
    iid = _start(engine, "u_dev_wu", amount=9000, duty="ceo")
    _intake(engine, iid)
    expect_position(engine, iid, nodes={"arm_fact"}, tasks={("arm_fact", "u_lead_be")})

    reject_of(engine, iid, "u_lead_be", comment="金额依据不足")
    expect_position(
        engine,
        iid,
        nodes={"start"},
        tasks={("start", "u_dev_wu")},
        note="回发起人重报",
    )
    complete_of(engine, iid, "u_dev_wu")
    _intake(engine, iid)
    expect_position(engine, iid, nodes={"arm_fact"}, tasks={("arm_fact", "u_lead_be")})
    complete_of(engine, iid, "u_lead_be")
    complete_of(engine, iid, "u_ceo")
    expect_position(engine, iid, nodes=set(), state="completed")

    gate_rows = [
        row
        for row in ((r.source_activity, r.selected) for r in engine.history.route_decisions(iid))
        if row[0] == "g_stage1"
    ]
    assert len(gate_rows) == 2, "一轮驳回产生两行路由审计（首走 + 重走）"
    assert all(row[1] == ["g_stage1->arm_fact"] for row in gate_rows)
    assert_no_dangling(engine)
