"""真实蓝图世界④⑤：BPO 财务域（收款到账 / 付款申请）。

收款到账文档要点：出纳到账登记 → 到账类型（结算/工伤/纠纷）→ 按类型对应
专员确认（结算/保险/调解）→ 按类型抄送部长 + 恒抄送企业服务中心主任。
付款申请文档要点（六类型多链）：六种付款类型走不同审批链，共享「董事长
审批 → 抄送财务总监 → 出纳付款上传凭证」尾段，链尾按类型决定有无发票复核：
- 结算：结算部长→董事长→…→结算专员复核
- 保险费：保险部长→董事长→…→保险专员复核
- 工伤：保险部长→董事长→…（无复核）
- 纠纷：调解部长→董事长→…（无复核）
- 办公室（默认臂）：办公室主任→董事长→…→办公室复核
- 销售提成：组长→总经理→董事长→…（四级，无复核）
付款类型分支用 first_match：office 挂默认边（兜住其他类型），其余五条条件边。
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable


def receipt_definition() -> ProcessDefinition:
    return (
        FlowBuilder("suite_receipt", "蓝图·收款到账登记")
        .start()
        .user_task("receipt_register", "到账登记", assignee="'bpo_cashier'")
        .branch("type_route", "到账类型分流")
        .user_task("settle_confirm", "结算专员确认", assignee="'bpo_set_sp'")
        .user_task("injury_confirm", "保险专员确认", assignee="'bpo_ins_sp'")
        .user_task("dispute_confirm", "调解专员确认", assignee="'bpo_med_sp'")
        .cc_task("cc_set_head", "抄送结算部长", recipients=["'bpo_set_head'"])
        .cc_task("cc_ins_head", "抄送保险部长", recipients=["'bpo_ins_head'"])
        .cc_task("cc_med_head", "抄送调解部长", recipients=["'bpo_med_head'"])
        .cc_task("cc_center", "抄送企服主任", recipients=["'bpo_srv_head'"])
        .end("receipt_end", "到账登记完成")
        .flow("start", "receipt_register")
        .flow("receipt_register", "type_route")
        .flow("type_route", "settle_confirm", condition='receipt_type == "settle"')
        .flow("type_route", "injury_confirm", condition='receipt_type == "injury"')
        .flow("type_route", "dispute_confirm", default=True)  # 纠纷兜默认
        .flow("settle_confirm", "cc_set_head")
        .flow("injury_confirm", "cc_ins_head")
        .flow("dispute_confirm", "cc_med_head")
        .flow("cc_set_head", "cc_center")
        .flow("cc_ins_head", "cc_center")
        .flow("cc_med_head", "cc_center")
        .flow("cc_center", "receipt_end")
        .build()
        .model_copy(
            update={
                "variables": [
                    ProcessVariable(
                        name="receipt_type", type="string", label="到账类型", sample="settle"
                    ),
                ]
            }
        )
    )


def payment_definition() -> ProcessDefinition:
    return (
        FlowBuilder("suite_payment", "蓝图·付款申请")
        .start()
        .branch("pay_type_route", "付款类型分流")
        # 六链各自的首审（工伤与保险费共用保险部长臂）
        .user_task(
            "settle_head_appr", "结算部长审批", assignee="'bpo_set_head'", reject_target="start"
        )
        .user_task(
            "ins_head_appr", "保险部长审批", assignee="'bpo_ins_head'", reject_target="start"
        )
        .user_task(
            "med_head_appr", "调解部长审批", assignee="'bpo_med_head'", reject_target="start"
        )
        .user_task(
            "office_head_appr", "办公室主任审批", assignee="'bpo_off_head'", reject_target="start"
        )
        .user_task("team_leader_appr", "组长审批", assignee="'bpo_tl'", reject_target="start")
        .user_task("gm_approve", "总经理审批", assignee="'bpo_gm'")
        .user_task("chairman_approve", "董事长审批", assignee="'bpo_chairman'")
        .cc_task("cc_fin_director", "抄送财务总监", recipients=["'bpo_fin_director'"])
        .user_task("cashier_pay", "出纳付款上传回单", assignee="'bpo_cashier'")
        .branch("review_route", "发票复核分流")
        .user_task("settle_review", "结算专员发票复核", assignee="'bpo_set_sp'")
        .user_task("ins_review", "保险专员发票复核", assignee="'bpo_ins_sp'")
        .user_task("office_review", "办公室发票复核", assignee="'bpo_office'")
        .end("pay_end", "付款完成")
        .flow("start", "pay_type_route")
        .flow("pay_type_route", "settle_head_appr", condition='pay_type == "settle"')
        # 工伤与保险费共用保险部长臂（文档：均经保险部部长）——合并条件避免重复连线
        .flow(
            "pay_type_route",
            "ins_head_appr",
            condition='pay_type == "insurance" || pay_type == "injury"',
        )
        .flow("pay_type_route", "med_head_appr", condition='pay_type == "dispute"')
        .flow("pay_type_route", "team_leader_appr", condition='pay_type == "sales"')
        .flow("pay_type_route", "office_head_appr", default=True)  # 办公室/其他兜默认
        .flow("settle_head_appr", "chairman_approve")
        .flow("ins_head_appr", "chairman_approve")
        .flow("med_head_appr", "chairman_approve")
        .flow("office_head_appr", "chairman_approve")
        .flow("team_leader_appr", "gm_approve")
        .flow("gm_approve", "chairman_approve")
        .flow("chairman_approve", "cc_fin_director")
        .flow("cc_fin_director", "cashier_pay")
        .flow("cashier_pay", "review_route")
        .flow("review_route", "settle_review", condition='pay_type == "settle"')
        .flow("review_route", "ins_review", condition='pay_type == "insurance"')
        .flow("review_route", "office_review", default=True)  # 办公室兜默认
        # 工伤/纠纷/销售提成无发票复核 → 直达结束（合并条件避免重复连线）
        .flow(
            "review_route",
            "pay_end",
            condition='pay_type == "injury" || pay_type == "dispute" || pay_type == "sales"',
        )
        .flow("settle_review", "pay_end")
        .flow("ins_review", "pay_end")
        .flow("office_review", "pay_end")
        .build()
        .model_copy(
            update={
                "variables": [
                    ProcessVariable(
                        name="pay_type", type="string", label="付款类型", sample="settle"
                    ),
                ]
            }
        )
    )
