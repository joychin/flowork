"""套件业务流程定义：报销 kitchen sink + 采购并行/子流程 + 供应商合规子流程。

覆盖目标（与 features.py 特性矩阵对应）：7 种节点、first_match/all_match、
默认边、结构化条件、并行分叉与 all 汇聚、并签/串签/比例会签、驳回链、
表达式取人、候选人认领、to_admins 兜底、input 契约与 searchable 变量。
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable
from flowork.definitions.flow import ConditionItem, ConditionOperator, FlowCondition
from flowork.definitions.node import MultiInstance

HIGH_AMOUNT = 5000  # 报销高额阈值（分支依据）

EXPENSE_INPUT = {
    "type": "object",
    "properties": {
        "amount": {"type": "number", "minimum": 0},
        "expense_type": {"type": "string", "enum": ["travel", "office", "training"]},
        "urgent": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": ["amount", "expense_type"],
}
EXPENSE_VARIABLES = [
    ProcessVariable(name="amount", type="number", label="金额", sample=1200, searchable=True),
    ProcessVariable(
        name="expense_type", type="string", label="费用类型", sample="office", searchable=True
    ),
    ProcessVariable(name="urgent", type="boolean", label="加急", sample=False, searchable=True),
    ProcessVariable(name="note", type="string", label="事由", sample=""),
]


def expense_definition() -> ProcessDefinition:
    """报销主场景：金额分流 → 快审/财务会签+逐级签批 → 传阅 → 自动打款。"""
    builder = (
        FlowBuilder("suite_expense", "套件·差旅报销")
        .start()
        .branch("amount_route", "金额分流")
        .user_task(
            "quick_review",
            "主管快审",
            assignee="manager_of()",
            auto_pass_originator=True,
            reject_target="start",
        )
        .user_task(
            "fin_countersign",
            "财务会签",
            assignees=["users_with_role(finance_approver)"],
            multi_instance=MultiInstance(
                mode="parallel", approve_exit=">=2", disapprove_exit=">=1"
            ),
            reject_target="start",
        )
        .user_task(
            "level_sign",
            "逐级签批",
            assignees=["u_cfo", "u_ceo"],
            multi_instance=MultiInstance(mode="sequential", approve_exit="all"),
        )
        .cc_task("cc_fin", "抄送财务总监", recipients=["u_cfo"])
        .service_task("pay", "自动打款", action="make_payment")
        .end("expense_end", "报销结束")
        .flow("start", "amount_route")
        .flow(
            "amount_route",
            "fin_countersign",
            condition=FlowCondition(
                groups=[
                    [ConditionItem(left="amount", operator=ConditionOperator.GE, right=HIGH_AMOUNT)]
                ]
            ),
        )
        .flow("amount_route", "quick_review", default=True)
        .flow("quick_review", "cc_fin")
        .flow("fin_countersign", "level_sign")
        .flow("level_sign", "cc_fin")
        .flow("cc_fin", "pay")
        .flow("pay", "expense_end")
    )
    return builder.build().model_copy(
        update={"input": EXPENSE_INPUT, "variables": EXPENSE_VARIABLES}
    )


SUPPLIER_INPUT = {
    "type": "object",
    "properties": {"supplier": {"type": "string"}},
    "required": ["supplier"],
}


def supplier_check_definition() -> ProcessDefinition:
    """合规子流程：被采购主流程 call_activity 调用（input/output 映射）。"""
    return (
        FlowBuilder("suite_supplier_check", "套件·供应商合规核查")
        .start()
        .user_task("check", "合规核查", assignee="u_compliance")
        .end("supplier_end", "核查结束")
        .flow("start", "check")
        .flow("check", "supplier_end")
        .build()
        .model_copy(
            update={
                "input": SUPPLIER_INPUT,
                "variables": [
                    ProcessVariable(name="supplier", type="string", label="供应商", sample="华铝")
                ],
            }
        )
    )


PURCHASE_INPUT = {
    "type": "object",
    "properties": {
        "budget": {"type": "number", "minimum": 0},
        "risk": {"type": "string", "enum": ["low", "high"]},
        "supplier": {"type": "string"},
    },
    "required": ["budget", "risk", "supplier"],
}


def purchase_definition() -> ProcessDefinition:
    """采购场景：all_match 并行分叉 → 认领/子流程双支路 → all 等齐 → 管理员兜底。"""
    builder = (
        FlowBuilder("suite_purchase", "套件·采购申请")
        .start()
        .user_task(
            "dept_review",
            "主管审批",
            assignee="manager_of()",
            reject_target="start",
        )
        .branch("fork_route", "并行分流", route_mode="all_match")
        .user_task("legal_track", "法务通道", assignee="u_ceo")
        .call_activity(
            "compliance_track",
            "合规子流程",
            process_code="suite_supplier_check",
            input_mappings={"supplier": "supplier"},
            output_mappings={"compliance": "compliance"},
        )
        .user_task(
            "final_sign",
            "会签收口（等齐）",
            assignees=["u_cfo", "u_compliance"],
            multi_instance=MultiInstance(mode="parallel", approve_exit=">=60%"),
            advance_policy="all",
        )
        .user_task(
            "admin_gate",
            "管理员兜底",
            assignees=["users_with_role(ghost_role)"],
            no_participant_policy="to_admins",
        )
        .service_task("pay_order", "采购打款", action="make_payment")
        .end("purchase_end", "采购结束")
        .flow("start", "dept_review")
        .flow("dept_review", "fork_route")
        .flow(
            "fork_route",
            "legal_track",
            condition=FlowCondition(
                groups=[[ConditionItem(left="budget", operator=ConditionOperator.GT, right=20000)]]
            ),
        )
        .flow(
            "fork_route",
            "compliance_track",
            condition=FlowCondition(
                groups=[[ConditionItem(left="risk", operator=ConditionOperator.EQ, right="high")]]
            ),
        )
        .flow("fork_route", "final_sign", default=True)
        .flow("legal_track", "final_sign")
        .flow("compliance_track", "final_sign")
        .flow("final_sign", "admin_gate")
        .flow("admin_gate", "pay_order")
        .flow("pay_order", "purchase_end")
    )
    return builder.build().model_copy(
        update={
            "input": PURCHASE_INPUT,
            "variables": [
                ProcessVariable(
                    name="budget", type="number", label="预算", sample=50000, searchable=True
                ),
                ProcessVariable(name="risk", type="string", label="风险", sample="high"),
                ProcessVariable(name="supplier", type="string", label="供应商", sample="华铝"),
            ],
        }
    )
