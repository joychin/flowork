"""真实蓝图走查④⑤：BPO 财务域（收款到账三类型 / 付款申请六链多长）。

对照文档：收款到账（出纳登记→按类型专员确认→按类型抄送部长+企服主任）；
付款申请（六类型走不同审批链：2-4 级不等，共享董事长→抄送财务总监→出纳
尾段，链尾按类型决定有无发票复核）。
"""

from __future__ import annotations

from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers, covers_node
from tests.suite.invariants import assert_no_dangling
from tests.suite.walkthrough import complete_of

RECEIPT_CONFIRM_BY_TYPE = {  # 文档：到账类型 → 确认专员
    "settle": "bpo_set_sp",
    "injury": "bpo_ins_sp",
    "dispute": "bpo_med_sp",
}


def _receipt(engine: ProcessEngine, receipt_type: str) -> str:
    return engine.runtime.start_process(
        "suite_receipt", start_user="bpo_cashier", variables={"receipt_type": receipt_type}
    )


def _cc_recipients(engine: ProcessEngine, iid: str) -> set[str]:
    return {
        item.recipient
        for item in engine.task.list_cc_tasks(recipient="")
        if _cc_of(engine, item, iid)
    }


def _cc_of(engine: ProcessEngine, item: object, iid: str) -> bool:
    return getattr(item, "instance_id", None) == iid


@covers("route.first_match", "ops.cc_read")
@covers_node(
    "suite_receipt",
    "start",
    "receipt_register",
    "type_route",
    "settle_confirm",
    "injury_confirm",
    "dispute_confirm",
    "cc_set_head",
    "cc_ins_head",
    "cc_med_head",
    "cc_center",
    "receipt_end",
)
def test_receipt_settle_type(suite_engine: ProcessEngine) -> None:
    """结算款：结算专员确认 → 抄送结算部长 + 企服主任。"""
    iid = _receipt(suite_engine, "settle")
    complete_of(suite_engine, iid, "bpo_cashier")
    complete_of(suite_engine, iid, "bpo_set_sp")
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert _cc_recipients(suite_engine, iid) == {"bpo_set_head", "bpo_srv_head"}
    assert_no_dangling(suite_engine)


@covers("route.formula")
def test_receipt_injury_type(suite_engine: ProcessEngine) -> None:
    """工伤款：保险专员确认 → 抄送保险部长 + 企服主任。"""
    iid = _receipt(suite_engine, "injury")
    complete_of(suite_engine, iid, "bpo_cashier")
    complete_of(suite_engine, iid, "bpo_ins_sp")
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert _cc_recipients(suite_engine, iid) == {"bpo_ins_head", "bpo_srv_head"}
    assert_no_dangling(suite_engine)


@covers("route.default_edge")
def test_receipt_dispute_type(suite_engine: ProcessEngine) -> None:
    """纠纷款（默认臂）：调解专员确认 → 抄送调解部长 + 企服主任。"""
    iid = _receipt(suite_engine, "dispute")
    complete_of(suite_engine, iid, "bpo_cashier")
    complete_of(suite_engine, iid, "bpo_med_sp")
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert _cc_recipients(suite_engine, iid) == {"bpo_med_head", "bpo_srv_head"}
    assert_no_dangling(suite_engine)


def _pay(engine: ProcessEngine, pay_type: str, start_user: str) -> str:
    return engine.runtime.start_process(
        "suite_payment", start_user=start_user, variables={"pay_type": pay_type}
    )


def _approval_chain(engine: ProcessEngine, iid: str) -> list[str]:
    """审批链（按办理顺序的节点序列，剔除 start/cc）。"""
    return [
        h.activity_code
        for h in engine.history.task_history(iid)
        if h.activity_code not in ("start",)
    ]


@covers("route.first_match", "query.cc_list")
@covers_node(
    "suite_payment",
    "start",
    "pay_type_route",
    "settle_head_appr",
    "chairman_approve",
    "cc_fin_director",
    "cashier_pay",
    "review_route",
    "settle_review",
    "pay_end",
)
def test_payment_settle_full_chain_with_review(suite_engine: ProcessEngine) -> None:
    """结算付款全链：结算部长→董事长→抄送财务总监→出纳付款→结算专员发票复核。"""
    iid = _pay(suite_engine, "settle", "bpo_set_sp")
    complete_of(suite_engine, iid, "bpo_set_head")
    complete_of(suite_engine, iid, "bpo_chairman")
    complete_of(suite_engine, iid, "bpo_cashier")
    complete_of(suite_engine, iid, "bpo_set_sp")  # 发票复核

    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert _approval_chain(suite_engine, iid) == [
        "settle_head_appr",
        "chairman_approve",
        "cashier_pay",
        "settle_review",
    ]
    assert _cc_recipients(suite_engine, iid) == {"bpo_fin_director"}
    assert_no_dangling(suite_engine)


@covers("route.formula")
@covers_node("suite_payment", "ins_head_appr")
def test_payment_injury_no_review(suite_engine: ProcessEngine) -> None:
    """工伤付款：保险部长→董事长→出纳，无发票复核（文档链③）。"""
    iid = _pay(suite_engine, "injury", "bpo_ins_sp")
    complete_of(suite_engine, iid, "bpo_ins_head")
    complete_of(suite_engine, iid, "bpo_chairman")
    complete_of(suite_engine, iid, "bpo_cashier")

    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert _approval_chain(suite_engine, iid) == [
        "ins_head_appr",
        "chairman_approve",
        "cashier_pay",
    ]
    assert_no_dangling(suite_engine)


@covers("reject.explicit_target")
@covers_node("suite_payment", "team_leader_appr", "gm_approve")
def test_payment_sales_four_levels(suite_engine: ProcessEngine) -> None:
    """销售提成付款：组长→总经理→董事长→出纳（四级链，文档链⑥）。"""
    iid = _pay(suite_engine, "sales", "bpo_am")
    complete_of(suite_engine, iid, "bpo_tl")
    complete_of(suite_engine, iid, "bpo_gm")
    complete_of(suite_engine, iid, "bpo_chairman")
    complete_of(suite_engine, iid, "bpo_cashier")

    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert _approval_chain(suite_engine, iid) == [
        "team_leader_appr",
        "gm_approve",
        "chairman_approve",
        "cashier_pay",
    ]
    assert_no_dangling(suite_engine)


@covers("route.default_edge", "query.task_history_append_only")
@covers_node("suite_payment", "office_head_appr", "office_review", "med_head_appr", "ins_review")
def test_payment_office_default_and_dispute(suite_engine: ProcessEngine) -> None:
    """办公室付款走默认臂且带复核；纠纷付款走调解部长无复核（两链合测）。"""
    office = _pay(suite_engine, "office", "bpo_office")
    complete_of(suite_engine, office, "bpo_off_head")
    complete_of(suite_engine, office, "bpo_chairman")
    complete_of(suite_engine, office, "bpo_cashier")
    complete_of(suite_engine, office, "bpo_office")  # 办公室发票复核
    assert suite_engine.runtime.get_instance(office).state == "completed"
    assert _approval_chain(suite_engine, office) == [
        "office_head_appr",
        "chairman_approve",
        "cashier_pay",
        "office_review",
    ]

    dispute = _pay(suite_engine, "dispute", "bpo_med_sp")
    complete_of(suite_engine, dispute, "bpo_med_head")
    complete_of(suite_engine, dispute, "bpo_chairman")
    complete_of(suite_engine, dispute, "bpo_cashier")
    assert suite_engine.runtime.get_instance(dispute).state == "completed"
    assert _approval_chain(suite_engine, dispute) == [
        "med_head_appr",
        "chairman_approve",
        "cashier_pay",
    ]
    assert_no_dangling(suite_engine)
