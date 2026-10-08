"""试点模板生成：四个贴近真实的复杂流程（报销/请假/采购/合同评审）。

运行 ``python examples/pilot/build_templates.py`` 重新生成 JSON（单一事实源的 DSL 侧）。
规则摘要见各 builder 注释；详细场景见 tests/pilot/。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.builtin import RouteMode
from flowork.definitions.definition import dump_definition
from flowork.definitions.node import MultiInstance
from flowork.definitions.validator import IssueLevel, validate_definition
from flowork.runtime.registry import create_registry

OUT = Path(__file__).parent


_OPS = {"eq": "==", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}


def cond(left: str, op: str, right: object) -> str:
    """单条件 → CEL 公式（条件单轨化，spec §4.1）；值按 CEL 字面量归一。"""
    if right is True:
        lit = "true"
    elif right is False:
        lit = "false"
    elif right is None:
        lit = "null"
    elif isinstance(right, str):
        lit = f'"{right}"'
    else:
        lit = str(right)
    return f"{left} {_OPS[op]} {lit}"


def expense_claim():
    """报销：金额三级分流。

    - <1000：主管直审 → 财务打款(service) → 结束
    - 1000~10000：主管 → 财务双人会签(>=50%，可驳回归主管) → 打款 → 结束
    - >10000：主管 → 总监(u_chen) → 财务会签 → 法务传阅(u_fa1) → 打款 → 结束
    - 主管节点可驳回到发起人（start，发起人重报）
    """
    return (
        FlowBuilder("expense_claim", "报销申请")
        .start("start", "提交")
        .user_task(
            "supervisor",
            "直属主管审批",
            assignee="manager_of(initiator)",
            reject_target="start",
        )
        .branch("amount_route", "金额分流", route_mode=RouteMode.FIRST_MATCH)
        .user_task(
            "finance_review",
            "财务会签",
            assignees=["'u_cai1'", "'u_cai2'"],
            multi_instance=MultiInstance(
                mode="parallel", approve_exit=">=50%", disapprove_exit=">=1"
            ),
            reject_target="supervisor",
            advance_policy="any",
        )
        .user_task("director_review", "总监审批", assignee="'u_chen'")
        .cc_task("legal_cc", "法务留档传阅", recipients=["'u_fa1'"])
        .service_task("payment", "财务打款", action="make_payment", advance_policy="any")
        .end("end", "结束")
        .connect("start", "supervisor")
        .connect("supervisor", "amount_route")
        .flow("amount_route", "director_review", condition=cond("amount", "gt", 10000))
        .flow("amount_route", "finance_review", condition=cond("amount", "ge", 1000))
        .flow("amount_route", "payment", default=True)
        .connect("director_review", "finance_review")
        .flow("finance_review", "legal_cc", condition=cond("amount", "gt", 10000))
        .flow("finance_review", "payment", default=True)
        .connect("legal_cc", "payment")
        .connect("payment", "end")
        .build()
    )


def leave_request():
    """请假：天数分流 + 串行 HR 会签。

    - ≤3 天：主管 → HR 备案(u_hr1) → 传阅发起人 → 结束
    - >3 天：主管 → HR 串行会签(u_hr1→u_hr2，all) → 传阅发起人 → 结束
    - 主管自审：发起人若即主管 auto_pass_originator
    """
    return (
        FlowBuilder("leave_request", "请假申请")
        .start("start", "提交")
        .user_task(
            "supervisor",
            "主管审批",
            assignee="manager_of(initiator)",
            auto_pass_originator=True,
            reject_target="start",
        )
        .branch("days_route", "天数分流", route_mode=RouteMode.FIRST_MATCH)
        .user_task(
            "hr_sequential",
            "HR 串行会签",
            assignees=["'u_hr1'", "'u_hr2'"],
            multi_instance=MultiInstance(
                mode="sequential", approve_exit="all", disapprove_exit=">=1"
            ),
            reject_target="supervisor",
        )
        .user_task("hr_record", "HR 备案", assignee="'u_hr1'")
        .cc_task("notify", "结果传阅发起人", recipients=["initiator"], advance_policy="any")
        .end("end", "结束")
        .connect("start", "supervisor")
        .connect("supervisor", "days_route")
        .flow("days_route", "hr_sequential", condition=cond("days", "gt", 3))
        .flow("days_route", "hr_record", default=True)
        .connect("hr_sequential", "notify")
        .connect("hr_record", "notify")
        .connect("notify", "end")
        .build()
    )


def supplier_compliance():
    """供应商合规检查（子流程模板）：核验 → 有问题则退回发起人重报 → 通过。"""
    return (
        FlowBuilder("supplier_compliance", "供应商合规检查")
        .start("start", "发起")
        .user_task("verify", "合规核验", assignee="'u_fa1'", reject_target="start")
        .end("end", "完成")
        .connect("start", "verify")
        .connect("verify", "end")
        .build()
    )


def purchase_order():
    """采购：金额分流 + 法务/财务同时进行（≥10万双条件扇出）+ ≥50万 总经理与子流程。

    - <10万：部门主管 → 结束
    - 10万~50万：主管 →（法务核验 ‖ 财务核价）等全部到齐 → 结束
    - ≥50万：主管 → 并行 → 等全部到齐(big_route) → 大额分流 → 总经理 →
      供应商合规子流程(同步) → 打款 → 结束
    - 财务核价节点配置超时自动通过（240 分钟）
    """
    return (
        FlowBuilder("purchase_order", "采购申请")
        .start("start", "提交")
        .user_task("dept_review", "部门主管审批", assignee="manager_of(initiator)", reject_target="start")
        .branch("amount_route", "金额分流", route_mode=RouteMode.ALL_MATCH)
        .user_task("legal_review", "法务核验", assignee="'u_fa1'")
        .user_task(
            "finance_pricing",
            "财务核价",
            assignee="'u_cai1'",
            reject_target="dept_review",
        )
        .branch("big_route", "大额分流", route_mode=RouteMode.FIRST_MATCH, advance_policy="all")
        .user_task("gm_review", "总经理审批", assignee="'u_yang'")
        .call_activity(
            "supplier_check",
            "供应商合规子流程",
            process_code="supplier_compliance",
            input_mappings={"supplier": "supplier"},
            output_mappings={"compliance": "compliance"},
        )
        .service_task("payment", "采购打款", action="make_payment")
        .end("end", "结束", advance_policy="any")
        .connect("start", "dept_review")
        .connect("dept_review", "amount_route")
        .flow("amount_route", "legal_review", condition=cond("amount", "ge", 100000))
        .flow("amount_route", "finance_pricing", condition=cond("amount", "ge", 100000))
        .flow("amount_route", "end", default=True)
        .connect("legal_review", "big_route")
        .connect("finance_pricing", "big_route")
        .flow("big_route", "gm_review", condition=cond("amount", "ge", 500000))
        .flow("big_route", "end", default=True)
        .connect("gm_review", "supplier_check")
        .connect("supplier_check", "payment")
        .connect("payment", "end")
        .build()
    )


def contract_review():
    """合同评审：命中的都走（多条件同时成立多走）+ 结束节点任一到达即过（先到取消兄弟）。

    - 法务必审；金额>10万 加财务核价；urgent==true 加总经理终审
    - 任一分支到达 end 即通过并取消其余在办分支
    - 任一审批节点驳回归法务重办
    """
    return (
        FlowBuilder("contract_review", "合同评审")
        .start("start", "提交")
        .user_task("legal_review", "法务审查", assignee="'u_fa1'", reject_target="start")
        .branch("risk_route", "风险分流", route_mode=RouteMode.ALL_MATCH)
        .user_task("finance_review", "财务核价", assignee="'u_cai1'", reject_target="legal_review")
        .user_task("gm_review", "总经理终审", assignee="'u_yang'", reject_target="legal_review")
        .end("end", "完成", advance_policy="any")
        .connect("start", "legal_review")
        .connect("legal_review", "risk_route")
        .flow("risk_route", "finance_review", condition=cond("amount", "gt", 100000))
        .flow("risk_route", "gm_review", condition=cond("urgent", "eq", True))
        .flow("risk_route", "end", default=True)
        .connect("finance_review", "end")
        .connect("gm_review", "end")
        .build()
    )


def main() -> None:
    registry = create_registry()
    templates = {
        "expense_claim": expense_claim,
        "leave_request": leave_request,
        "supplier_compliance": supplier_compliance,
        "purchase_order": purchase_order,
        "contract_review": contract_review,
    }
    for name, builder_fn in templates.items():
        definition = builder_fn()
        issues = [
            issue
            for issue in validate_definition(definition, registry)
            if issue.level == IssueLevel.ERROR
        ]
        if issues:
            raise SystemExit(f"{name} 校验失败：{issues}")
        path = OUT / f"{name}.json"
        path.write_text(
            json.dumps(dump_definition(definition), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"✓ {name}: {len(definition.nodes)} 节点 / {len(definition.flows)} 连线")


if __name__ == "__main__":
    main()
