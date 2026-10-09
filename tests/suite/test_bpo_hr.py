"""真实蓝图走查②③：BPO 人事域（任务发布 / 人员合同签订含入职子流程）。

对照文档：任务发布 BS-PRJ-004（参保判断分支、项目部领导审批、抄送三专员）；
人员合同签订 BS-FILES-002（入职任务子流程发起、电子/纸质合同分支、
E签宝对接、档案专员确认与归档）。
"""

from __future__ import annotations

from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers, covers_node
from tests.suite.invariants import assert_no_dangling
from tests.suite.walkthrough import complete_of, reject_of

CC_STAKEHOLDERS = {"bpo_set_sp", "bpo_ins_sp", "bpo_card_clerk"}


def _publish(engine: ProcessEngine, has_insurance: bool) -> str:
    return engine.runtime.start_process(
        "suite_task_publish", start_user="bpo_pm", variables={"has_insurance": has_insurance}
    )


@covers("route.first_match", "ops.cc_read", "query.cc_list")
@covers_node(
    "suite_task_publish",
    "start",
    "insurance_route",
    "fetch_roster",
    "prj_head_approve",
    "cc_stakeholders",
    "publish_end",
)
def test_task_publish_with_insurance_roster(suite_engine: ProcessEngine) -> None:
    """含参保任务：系统获取未参保人员清单 → 项目部领导审批 → 抄送三专员。"""
    iid = _publish(suite_engine, has_insurance=True)
    assert suite_engine.runtime.get_instance(iid).state == "running"  # service 自动过，停在审批
    assert suite_engine.runtime.variables_of(iid)["roster_count"] == 3

    complete_of(suite_engine, iid, "bpo_prj_head")
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    recipients = {
        item.recipient for item in suite_engine.task.list_cc_tasks(recipient="") if item.recipient
    }  # 全库取（本测试独库，无他实例混入）
    assert recipients >= CC_STAKEHOLDERS
    assert_no_dangling(suite_engine)


@covers("route.formula")
def test_task_publish_without_insurance(suite_engine: ProcessEngine) -> None:
    """无参保任务：跳过人员清单获取（令牌不产生），直接审批抄送。"""
    iid = _publish(suite_engine, has_insurance=False)
    tokens = {t.activity_code for t in suite_engine.history.tokens(iid)}
    assert "fetch_roster" not in tokens
    assert suite_engine.runtime.variables_of(iid).get("roster_count") is None  # 未执行

    complete_of(suite_engine, iid, "bpo_prj_head")
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(suite_engine)


@covers("reject.to_start")
def test_task_publish_reject_to_initiator(suite_engine: ProcessEngine) -> None:
    """项目部领导驳回 → 回项目经理重发。"""
    iid = _publish(suite_engine, has_insurance=True)
    reject_of(suite_engine, iid, "bpo_prj_head", comment="任务类型与订单保险类型不符")
    start_tasks = suite_engine.task.list_tasks(instance_id=iid, assignee="bpo_pm")
    assert len(start_tasks) == 1

    suite_engine.task.complete(start_tasks[0].id, actor="bpo_pm")
    complete_of(suite_engine, iid, "bpo_prj_head")
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(suite_engine)


@covers("node.call_activity", "subprocess.output_mapping")
@covers_node(
    "suite_contract_sign",
    "start",
    "onboard_task",
    "contract_input",
    "e_contract_route",
    "esign_send",
    "material_confirm",
    "archive",
    "contract_end",
)
@covers_node("suite_onboard_task", "start", "onboard_publish", "onboard_end")
def test_contract_sign_electronic(suite_engine: ProcessEngine) -> None:
    """电子合同链：入职子流程 → 录入 → E签宝签订 → 档案确认 → 归档。"""
    iid = suite_engine.runtime.start_process(
        "suite_contract_sign", start_user="bpo_pm", variables={"is_electronic": True}
    )
    # 子流程已启动：入职任务待办在子实例
    children = [r for r in suite_engine.history.instances() if r.parent_id == iid]
    assert len(children) == 1 and children[0].state == "running"
    complete_of(suite_engine, children[0].id, "bpo_pm", variables={"entry_date": "2026-10-01"})

    # 父流程恢复：录入合同信息（项目经理）
    complete_of(suite_engine, iid, "bpo_pm", variables={"contract_no": "HT-001"})
    # E签宝自动签订 → 档案专员材料确认 + 归档
    assert suite_engine.runtime.variables_of(iid)["esign_status"] == "signed"
    assert suite_engine.runtime.variables_of(iid)["hire_confirmed"] == "2026-10-01"  # 子→父映射
    complete_of(suite_engine, iid, "bpo_archivist")
    complete_of(suite_engine, iid, "bpo_archivist", variables={"archive_code": "DA-2026-001"})

    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert suite_engine.runtime.variables_of(iid)["archive_code"] == "DA-2026-001"
    assert_no_dangling(suite_engine)


@covers("route.default_edge")
def test_contract_sign_paper_skips_esign(suite_engine: ProcessEngine) -> None:
    """纸质合同：跳过 E签宝（令牌不产生），直接档案确认归档。"""
    iid = suite_engine.runtime.start_process(
        "suite_contract_sign", start_user="bpo_pm", variables={"is_electronic": False}
    )
    children = [r for r in suite_engine.history.instances() if r.parent_id == iid]
    complete_of(suite_engine, children[0].id, "bpo_pm", variables={"entry_date": "2026-10-02"})

    complete_of(suite_engine, iid, "bpo_pm")
    tokens = {t.activity_code for t in suite_engine.history.tokens(iid)}
    assert "esign_send" not in tokens
    assert suite_engine.runtime.variables_of(iid).get("esign_status") is None

    complete_of(suite_engine, iid, "bpo_archivist")
    complete_of(suite_engine, iid, "bpo_archivist", variables={"archive_code": "DA-2026-002"})
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(suite_engine)
