"""真实蓝图世界②③：BPO 人事域（任务发布 BS-PRJ-004 / 人员合同签订 BS-FILES-002）。

任务发布文档要点：项目经理发起 → 是否存在参保任务（分支）→ 参保时系统
获取未参保人员清单 → 项目部领导审批 → 抄送（结算部/保险部/办卡专员）。
合同签订文档要点：入职任务（业务子流程）→ 项目经理录入合同信息 → 是否
电子合同（分支：电子走 E签宝对接签订）→ 档案专员材料确认 → 盖章归档。
- fetch_roster handler 写 roster_count（外部档案系统由测试变量模拟）
- esign_send handler 写 esign_status（E签宝由测试变量模拟）
- 归档编码由档案专员 complete 时带 variables 写入（无 handler）
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable


def onboard_task_definition() -> ProcessDefinition:
    """入职任务子流程：项目经理确认入职信息后发布（被合同签订主流程调用）。"""
    return (
        FlowBuilder("suite_onboard_task", "蓝图·入职任务发布")
        .start()
        .user_task("onboard_publish", "发布入职任务", assignee="'bpo_pm'")
        .end("onboard_end", "入职任务完成")
        .flow("start", "onboard_publish")
        .flow("onboard_publish", "onboard_end")
        .build()
        .model_copy(
            update={
                "variables": [
                    ProcessVariable(name="entry_date", type="string", label="入职日期", sample="")
                ]
            }
        )
    )


def task_publish_definition() -> ProcessDefinition:
    return (
        FlowBuilder("suite_task_publish", "蓝图·任务发布")
        .start()
        .branch("insurance_route", "是否存在参保任务")
        .service_task("fetch_roster", "获取未参保人员清单", action="fetch_roster")
        .user_task(
            "prj_head_approve", "项目部领导审批", assignee="'bpo_prj_head'", reject_target="start"
        )
        .cc_task(
            "cc_stakeholders",
            "抄送各专员",
            recipients=["'bpo_set_sp'", "'bpo_ins_sp'", "'bpo_card_clerk'"],
        )
        .end("publish_end", "任务发布完成")
        .flow("start", "insurance_route")
        .flow("insurance_route", "fetch_roster", condition="has_insurance == true")
        .flow("insurance_route", "prj_head_approve", default=True)
        .flow("fetch_roster", "prj_head_approve")
        .flow("prj_head_approve", "cc_stakeholders")
        .flow("cc_stakeholders", "publish_end")
        .build()
        .model_copy(
            update={
                "variables": [
                    ProcessVariable(
                        name="has_insurance", type="boolean", label="含参保任务", sample=True
                    ),
                    ProcessVariable(name="roster_count", type="number", label="名单人数", sample=0),
                ]
            }
        )
    )


def contract_sign_definition() -> ProcessDefinition:
    return (
        FlowBuilder("suite_contract_sign", "蓝图·人员合同签订")
        .start()
        .call_activity(
            "onboard_task",
            "入职任务子流程",
            process_code="suite_onboard_task",
            output_mappings={"hire_confirmed": "entry_date"},
        )
        .user_task("contract_input", "录入合同签订信息", assignee="'bpo_pm'", reject_target="start")
        .branch("e_contract_route", "是否电子合同")
        .service_task("esign_send", "对接E签宝签订", action="esign_send")
        .user_task("material_confirm", "入职材料确认", assignee="'bpo_archivist'")
        .user_task("archive", "合同盖章归档", assignee="'bpo_archivist'")
        .end("contract_end", "合同归档完成")
        .flow("start", "onboard_task")
        .flow("onboard_task", "contract_input")
        .flow("contract_input", "e_contract_route")
        .flow("e_contract_route", "esign_send", condition="is_electronic == true")
        .flow("e_contract_route", "material_confirm", default=True)
        .flow("esign_send", "material_confirm")
        .flow("material_confirm", "archive")
        .flow("archive", "contract_end")
        .build()
        .model_copy(
            update={
                "variables": [
                    ProcessVariable(
                        name="is_electronic", type="boolean", label="电子合同", sample=True
                    ),
                    ProcessVariable(
                        name="esign_status", type="string", label="E签宝状态", sample=""
                    ),
                    ProcessVariable(
                        name="hire_confirmed", type="string", label="入职完成", sample=""
                    ),
                ]
            }
        )
    )
