"""试点场景：合同评审（命中的都走多分支 / 任一到达即过并取消兄弟 / 驳回 / 版本演进快照隔离）。"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.builtin import RouteMode
from flowork.definitions.definition import ProcessVariable
from flowork.engine.engine import ProcessEngine
from tests.pilot.conftest import activities_of, complete_of, reject_of
from tests.suite.features import covers


def start_contract(
    pilot: ProcessEngine, amount: int = 50000, urgent: bool = False, user: str = "u_zhang"
) -> str:
    return pilot.runtime.start_process(
        "contract_review", start_user=user, variables={"amount": amount, "urgent": urgent}
    )


@covers("route.default_edge")
def test_contract_legal_only_default(pilot: ProcessEngine):
    """小额非紧急：只走法务 → 默认边直达或汇聚 → 完成。"""
    instance_id = start_contract(pilot, amount=50000)
    complete_of(pilot, instance_id, "u_fa1")
    assert pilot.runtime.get_instance(instance_id).state == "completed"


@covers("route.all_match", "join.all_gate")
def test_contract_finance_branch_only(pilot: ProcessEngine):
    """大额非紧急：法务 + 财务；财务先到或汇聚直接通过。"""
    instance_id = start_contract(pilot, amount=200000)
    complete_of(pilot, instance_id, "u_fa1")
    assert {a for a, _ in activities_of(pilot, instance_id)} == {"finance_review"}
    complete_of(pilot, instance_id, "u_cai1")
    assert pilot.runtime.get_instance(instance_id).state == "completed"


@covers("route.all_match", "join.any_absorb", "reject.cancel_sibling_tokens")
def test_contract_urgent_both_branches(pilot: ProcessEngine):
    """大额紧急：财务 + 总经理两分支同时激活；或汇聚任一到达即通过并取消另一分支。"""
    instance_id = start_contract(pilot, amount=200000, urgent=True)
    complete_of(pilot, instance_id, "u_fa1")
    assert {a for a, _ in activities_of(pilot, instance_id)} == {"finance_review", "gm_review"}

    # 总经理先办 → 或汇聚通过 → 财务分支被取消
    complete_of(pilot, instance_id, "u_yang")
    assert pilot.runtime.get_instance(instance_id).state == "completed"
    finance_token = next(
        t for t in pilot.history.tokens(instance_id) if t.activity_code == "finance_review"
    )
    assert finance_token.state == "cancelled"
    outcomes = [(h.activity_code, h.outcome) for h in pilot.history.task_history(instance_id)]
    # 财务从未真正办理（agree），其待办以 cancelled 留痕
    assert ("finance_review", "agree") not in outcomes
    assert ("finance_review", "cancelled") in outcomes


@covers("join.any_absorb", "reject.cancel_sibling_tokens")
def test_contract_finance_first_then_gm_cancelled(pilot: ProcessEngine):
    """对偶场景：财务先到 → 总经理分支被取消。"""
    instance_id = start_contract(pilot, amount=200000, urgent=True)
    complete_of(pilot, instance_id, "u_fa1")
    complete_of(pilot, instance_id, "u_cai1")
    assert pilot.runtime.get_instance(instance_id).state == "completed"
    gm_token = next(t for t in pilot.history.tokens(instance_id) if t.activity_code == "gm_review")
    assert gm_token.state == "cancelled"
    assert pilot.task.list_tasks(instance_id=instance_id) == []  # 总经理待办被撤


@covers("reject.node_target")
def test_contract_finance_reject_to_legal(pilot: ProcessEngine):
    """财务驳回归法务重办。"""
    instance_id = start_contract(pilot, amount=200000)
    complete_of(pilot, instance_id, "u_fa1")
    reject_of(pilot, instance_id, "u_cai1", comment="付款条款有坑")
    assert activities_of(pilot, instance_id) == [("legal_review", "u_fa1")]
    complete_of(pilot, instance_id, "u_fa1", comment="已修订条款")
    complete_of(pilot, instance_id, "u_cai1")
    assert pilot.runtime.get_instance(instance_id).state == "completed"


@covers("reject.node_target", "reject.cancel_sibling_tokens")
def test_contract_gm_reject_to_legal(pilot: ProcessEngine):
    """总经理驳回归法务（另一分支目标一致性）。"""
    instance_id = start_contract(pilot, amount=200000, urgent=True)
    complete_of(pilot, instance_id, "u_fa1")
    reject_of(pilot, instance_id, "u_yang", comment="风险过高")
    assert activities_of(pilot, instance_id) == [("legal_review", "u_fa1")]
    # 兄弟财务分支同时被取消
    finance_token = next(
        t for t in pilot.history.tokens(instance_id) if t.activity_code == "finance_review"
    )
    assert finance_token.state == "cancelled"


@covers("version.instance_snapshot", "route.formula")
def test_contract_version_snapshot_isolation(pilot: ProcessEngine):
    """版本演进：v1 在途实例按发起时快照跑完，v2 新规则只影响新实例。

    v2 变更：财务分支阈值 100000 → 80000（小额合同也要财务核价）。
    """
    # v1 实例在途（法务已办，财务分支未激活——50000 < 100000 走默认边已完成汇聚前）
    v1_instance = start_contract(pilot, amount=90000)
    complete_of(pilot, v1_instance, "u_fa1")
    assert pilot.runtime.get_instance(v1_instance).state == "completed"  # v1: 无需财务

    # 发布 v2：阈值降到 80000

    revised = (
        FlowBuilder("contract_review", "合同评审 v2")
        .start("start", "提交")
        .user_task("legal_review", "法务审查", assignee="'u_fa1'", reject_target="start")
        .branch("risk_route", "风险分流", route_mode=RouteMode.ALL_MATCH)
        .user_task("finance_review", "财务核价", assignee="'u_cai1'", reject_target="legal_review")
        .user_task("gm_review", "总经理终审", assignee="'u_yang'", reject_target="legal_review")
        .end("end", "完成", advance_policy="any")
        .connect("start", "legal_review")
        .connect("legal_review", "risk_route")
        .flow("risk_route", "finance_review", condition="amount > 80000")
        .flow("risk_route", "gm_review", condition="urgent == true")
        .flow("risk_route", "end", default=True)
        .connect("finance_review", "end")
        .connect("gm_review", "end")
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
    pilot.repository.deploy(revised)

    # v2 新实例：90000 > 80000 → 需要财务核价
    v2_instance = start_contract(pilot, amount=90000)
    complete_of(pilot, v2_instance, "u_fa1")
    assert {a for a, _ in activities_of(pilot, v2_instance)} == {"finance_review"}  # v2 规则生效
    complete_of(pilot, v2_instance, "u_cai1")
    assert pilot.runtime.get_instance(v2_instance).state == "completed"

    # v1 实例的历史不含财务（快照语义回顾）
    v1_activities = {t.activity_code for t in pilot.history.tokens(v1_instance)}
    assert "finance_review" not in v1_activities


@covers("ops.transfer")
def test_contract_claim_and_transfer_combo(pilot: ProcessEngine):
    """认领 + 转办组合：法务任务转给同事，同事又转回后办理。"""
    instance_id = start_contract(pilot, amount=200000)
    fa_task = pilot.task.list_tasks(instance_id=instance_id, assignee="u_fa1")[0]
    moved = pilot.task.transfer(fa_task.id, "u_wang", actor="u_fa1", comment="出差代办")
    assert moved.assignee == "u_wang"
    pilot.task.transfer(moved.id, "u_fa1", actor="u_wang")
    complete_of(pilot, instance_id, "u_fa1")
    complete_of(pilot, instance_id, "u_cai1")
    assert pilot.runtime.get_instance(instance_id).state == "completed"
    transfers = [h for h in pilot.history.task_history(instance_id) if h.outcome == "transfer"]
    assert len(transfers) == 2  # 两次转办留痕
