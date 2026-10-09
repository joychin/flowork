"""真实蓝图世界①：金赛《系统物料主数据-产成品研发产品审批单》（BPM_10 V1.5）。

文档要点 → 建模对照：
- 「提交到其他所有视图的流程负责人」→ view_fork all_match 六臂并行填写
  （MRP/质量QA/质量QC/销售/财务无条件；仓库视图按文档「未进行仓位管理可不填」
  条件参与 need_warehouse）
- 各视图负责人可驳回申请人 → 六个填写位 reject_target=start
- 「主数据管理组审核」→ mdm_review advance_policy=all 等齐 + 角色取人
- SAP【检查】失败「由物料主数据管理员指定视图归口负责人二次填写提交」→
  check_route 失败臂 fix_fill（assignee=变量 fixer_var 动态取人）→ 回 sap_check
  修复环（引擎回环流转，实验已验证）
- SAP 返回 S（物料编码）/E（失败）→ result_route 二分支，E 走管理员失败处理
- handler 语义：sap_check 读 mock_check_ok 写 sap_check_ok；sap_submit 读
  mock_sap_result 写 sap_code/material_code（S 时 MAT-7001）——外部系统由测试变量模拟
- 文档标注「取消」的「指定维护人」环节不建模
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable

VIEW_OWNERS = {  # 视图 → 归口角色（文档：填写人1-6）
    "mrp_fill": "mrp_owner",
    "qa_fill": "qa_owner",
    "qc_fill": "qc_owner",
    "wh_fill": "wh_owner",
    "sales_fill": "sales_view_owner",
    "fin_fill": "fin_view_owner",
}


def material_master_definition() -> ProcessDefinition:
    builder = (
        FlowBuilder("suite_material_master", "蓝图·物料主数据审批单")
        .start()
        .branch("view_fork", "视图并行分发", route_mode="all_match")
    )
    for code in VIEW_OWNERS:
        builder.user_task(
            code,
            "视图归口填写",
            assignees=[f'users_with_role("{VIEW_OWNERS[code]}")'],
            reject_target="start",
        )
    builder.user_task(
        "mdm_review",
        "主数据管理组审核",
        assignees=['users_with_role("mdm_admin")'],
        advance_policy="all",
        reject_target="start",
    )
    builder.service_task("sap_check", "SAP数据检查", action="sap_check")
    builder.branch("check_route", "检查结果分流")
    builder.user_task("fix_fill", "归口人二次填写", assignee="fixer_var")
    builder.service_task("sap_submit", "提交SAP创建", action="sap_submit")
    builder.branch("result_route", "SAP结果分流")
    builder.user_task("fail_handle", "SAP失败处理", assignees=['users_with_role("mdm_admin")'])
    builder.end("mat_end", "物料建档结束")

    builder.flow("start", "view_fork")
    for code in VIEW_OWNERS:
        if code == "wh_fill":
            builder.flow("view_fork", code, condition="need_warehouse == true")
        else:
            # 六臂全条件化：五臂恒真（无条件边会被校验器要求默认边，
            # 而 all_match 下默认边永不触发——挂谁谁失效，故用恒真条件）
            builder.flow("view_fork", code, condition="true")
        builder.flow(code, "mdm_review")
    builder.flow("mdm_review", "sap_check")
    builder.flow("sap_check", "check_route")
    builder.flow("check_route", "fix_fill", condition="sap_check_ok != true")
    builder.flow("check_route", "sap_submit", default=True)
    builder.flow("fix_fill", "sap_check")
    builder.flow("sap_submit", "result_route")
    builder.flow("result_route", "fail_handle", condition='sap_code == "E"')
    builder.flow("result_route", "mat_end", default=True)
    builder.flow("fail_handle", "mat_end")
    return builder.build().model_copy(
        update={
            "variables": [
                ProcessVariable(
                    name="need_warehouse", type="boolean", label="需仓库视图", sample=True
                ),
                ProcessVariable(
                    name="fixer_var", type="string", label="指定归口人", sample="jin_qa"
                ),
                ProcessVariable(
                    name="mock_check_ok", type="boolean", label="检查通过", sample=True
                ),
                ProcessVariable(name="mock_sap_result", type="string", label="SAP返回", sample="S"),
                ProcessVariable(name="sap_check_ok", type="boolean", label="检查结果", sample=True),
                ProcessVariable(name="sap_code", type="string", label="SAP编码", sample=""),
                ProcessVariable(name="material_code", type="string", label="物料编码", sample=""),
            ]
        }
    )
