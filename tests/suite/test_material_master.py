"""真实蓝图走查①：金赛物料主数据审批单（六视图并行 / 仓库条件视图 / SAP 修复环 / S-E 结局）。

对照文档（BPM_10 V1.5）：「提交到其他所有视图的流程负责人」「未进行仓位管理
可不用填写仓库视图」「检查有误由物料主数据管理员指定归口负责人二次填写提交」
「SAP 返回 S 反馈物料编码 / E 反馈失败原因」。
"""

from __future__ import annotations

from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers, covers_node
from tests.suite.invariants import assert_no_dangling
from tests.suite.sequence_audit import route_of
from tests.suite.walkthrough import complete_of, reject_of

FILL_OWNERS = [  # 六视图归口人（角色唯一持有者）
    "jin_planner",  # MRP
    "jin_qa",  # 质量QA
    "jin_qc",  # 质量QC
    "jin_keeper",  # 仓库
    "jin_sales_rep",  # 销售
    "jin_fin_clerk",  # 财务
]


def _start(engine: ProcessEngine, **overrides: object) -> str:
    variables: dict[str, object] = {
        "need_warehouse": True,
        "fixer_var": "jin_qa",
        "mock_check_ok": True,
        "mock_sap_result": "S",
        **overrides,
    }
    return engine.runtime.start_process(
        "suite_material_master", start_user="jin_buyer", variables=variables
    )


@covers("route.all_match", "join.all_gate", "participant.expression_users_with_role")
@covers_node(
    "suite_material_master",
    "start",
    "view_fork",
    "mrp_fill",
    "qa_fill",
    "sales_fill",
    "fin_fill",
    "mdm_review",
    "sap_check",
    "check_route",
    "sap_submit",
    "result_route",
    "mat_end",
)
def test_full_views_parallel_then_sap_success(suite_engine: ProcessEngine) -> None:
    """六视图并行填写→等齐审核→SAP 检查通过→创建成功回填物料编码。"""
    iid = _start(suite_engine)
    assert {t.assignee for t in suite_engine.task.list_tasks(instance_id=iid)} == set(FILL_OWNERS)

    for owner in FILL_OWNERS:
        complete_of(suite_engine, iid, owner)
    complete_of(suite_engine, iid, "jin_mdm")  # 主数据管理组审核（等齐六视图后到达）

    assert suite_engine.runtime.get_instance(iid).state == "completed"
    variables = suite_engine.runtime.variables_of(iid)
    assert variables["sap_code"] == "S"
    assert variables["material_code"] == "MAT-7001"  # SAP 反馈物料编码
    fork = route_of(suite_engine, iid, "view_fork")
    assert ("view_fork->wh_fill", "true") in fork["edges"]  # 仓库条件臂命中
    assert_no_dangling(suite_engine)


@covers("route.formula")
@covers_node("suite_material_master", "wh_fill", "qc_fill")
def test_non_warehouse_material_skips_wh_view(suite_engine: ProcessEngine) -> None:
    """非仓库物料：五视图参与，仓库臂条件不命中、令牌不产生。"""
    iid = _start(suite_engine, need_warehouse=False)
    ready = {t.assignee for t in suite_engine.task.list_tasks(instance_id=iid)}
    assert ready == set(FILL_OWNERS) - {"jin_keeper"}

    for owner in ready:
        complete_of(suite_engine, iid, owner)
    complete_of(suite_engine, iid, "jin_mdm")
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    tokens = {t.activity_code for t in suite_engine.history.tokens(iid)}
    assert "wh_fill" not in tokens  # 从未激活
    fork = route_of(suite_engine, iid, "view_fork")
    assert ("view_fork->wh_fill", "false") in fork["edges"]
    assert_no_dangling(suite_engine)


@covers("reject.explicit_target", "reject.cause_token")
def test_view_owner_rejects_to_applicant(suite_engine: ProcessEngine) -> None:
    """视图归口人驳回到申请人（文档：各视图负责人可驳回）→ 重报重走。"""
    iid = _start(suite_engine)
    complete_of(suite_engine, iid, "jin_planner")
    reject_of(suite_engine, iid, "jin_qa", comment="基本视图缺物料组")

    start_tasks = suite_engine.task.list_tasks(instance_id=iid, assignee="jin_buyer")
    assert len(start_tasks) == 1  # 申请人重报任务
    suite_engine.task.complete(start_tasks[0].id, actor="jin_buyer")

    for owner in FILL_OWNERS:  # 全量重走
        complete_of(suite_engine, iid, owner)
    complete_of(suite_engine, iid, "jin_mdm")
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    outcomes = [(h.activity_code, h.outcome) for h in suite_engine.history.task_history(iid)]
    assert ("qa_fill", "reject") in outcomes
    assert_no_dangling(suite_engine)


@covers("subprocess.child_recover", "variables.reroute_pending_branch")
@covers_node("suite_material_master", "fix_fill")
def test_sap_check_failure_triggers_repair_loop(suite_engine: ProcessEngine) -> None:
    """SAP 检查失败→管理员指定的归口人二次填写（变量取人）→回检查→通过。

    文档：「如数据有误，由物料主数据管理员指定视图信息归口负责人进行
    二次填写提交」——fix_fill 的 assignee 是变量 fixer_var，运行时动态取人。
    """
    iid = _start(suite_engine, mock_check_ok=False, fixer_var="jin_planner")
    for owner in FILL_OWNERS:
        complete_of(suite_engine, iid, owner)
    complete_of(suite_engine, iid, "jin_mdm")

    # 检查失败：二次填写任务落在管理员指定的归口人（jin_planner）手上
    fix_tasks = suite_engine.task.list_tasks(instance_id=iid, assignee="jin_planner")
    assert [t.activity_code for t in fix_tasks] == ["fix_fill"]

    # 归口人修正数据后提交 → 回 SAP 检查 → 第二次通过 → 创建成功
    suite_engine.task.complete(
        fix_tasks[0].id, actor="jin_planner", variables={"mock_check_ok": True}
    )
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    check_rounds = [t for t in suite_engine.history.tokens(iid) if t.activity_code == "sap_check"]
    assert len(check_rounds) == 2  # 修复环：检查执行了两轮
    assert suite_engine.runtime.variables_of(iid)["material_code"] == "MAT-7001"
    assert_no_dangling(suite_engine)


@covers("fault.business_no_incident", "query.tokens")
@covers_node("suite_material_master", "fail_handle")
def test_sap_error_routes_to_failure_handling(suite_engine: ProcessEngine) -> None:
    """SAP 返回 E：走管理员失败处理，不产生物料编码。"""
    iid = _start(suite_engine, mock_sap_result="E")
    for owner in FILL_OWNERS:
        complete_of(suite_engine, iid, owner)
    complete_of(suite_engine, iid, "jin_mdm")
    complete_of(suite_engine, iid, "jin_mdm")  # fail_handle 亦由主数据管理员处理

    assert suite_engine.runtime.get_instance(iid).state == "completed"
    variables = suite_engine.runtime.variables_of(iid)
    assert variables["sap_code"] == "E"
    assert not variables.get("material_code")
    assert_no_dangling(suite_engine)
