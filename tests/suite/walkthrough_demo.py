"""手动走查演示（顺路场景）：实际运行套件测试数据，一个审批节点一个审批节点地查看。

运行：``uv run python -m tests.suite.walkthrough_demo``
驳回与拒收专场见 ``walkthrough_reject_demo``；时限与故障专场见 ``walkthrough_fault_demo``。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from flowork.engine.engine import ProcessEngine
from tests.suite.demo_kit import (
    archived,
    make_static_engine,
    make_store_engine,
    step,
    who,
)
from tests.suite.walkthrough import complete_of, entered_activities, reject_of


def scenario_expense_high_lane(engine: ProcessEngine) -> None:
    print("\n场景 A｜报销 8000 元——高额道：财务会签 → 逐级签批 → 传阅 → 自动打款")
    iid = engine.runtime.start_process(
        "suite_expense",
        start_user="u_dev_wu",
        variables={"amount": 8000, "expense_type": "travel"},
    )
    print(f"  发起实例 {iid[:8]}：{who('u_dev_wu')} 提交差旅报销 8000 元")

    step(
        engine,
        iid,
        "第 1 步 发起（金额≥5000 走高额道）",
        nodes={"fin_countersign"},
        tasks={("fin_countersign", "u_chief_acc"), ("fin_countersign", "u_cash")},
    )
    complete_of(engine, iid, "u_chief_acc", comment="票据齐全")
    print(f"    ✓ 已办：{archived(engine, iid)}")
    step(
        engine,
        iid,
        "第 2 步 孙会计同意（1/2，未达 >=2 出口）",
        nodes={"fin_countersign"},
        tasks={("fin_countersign", "u_cash")},
    )

    complete_of(engine, iid, "u_cash", comment="复核无误")
    print(f"    ✓ 已办：{archived(engine, iid)}")
    step(
        engine,
        iid,
        "第 3 步 周出纳同意（2/2 出口达成 → 进串签）",
        nodes={"level_sign"},
        tasks={("level_sign", "u_cfo")},
    )

    complete_of(engine, iid, "u_cfo", comment="额度内同意")
    step(
        engine,
        iid,
        "第 4 步 CFO 钱总同意（串签下一位）",
        nodes={"level_sign"},
        tasks={("level_sign", "u_ceo")},
    )

    complete_of(engine, iid, "u_ceo", comment="准予报销")
    step(engine, iid, "第 5 步 CEO 张总同意（传阅→打款自动通过）", nodes=set(), state="completed")
    assert engine.task.list_cc_tasks(recipient="u_cfo"), "传阅件应生成"

    expected = [
        "start",
        "amount_route",
        "fin_countersign",
        "level_sign",
        "cc_fin",
        "pay",
        "expense_end",
    ]
    entered = entered_activities(engine, iid)
    assert entered == expected, f"节点序列不符：{entered}"
    print(f"    ✓ 节点进入序列与预期一致：{' → '.join(expected)}")


def scenario_expense_reject_reroute(engine: ProcessEngine) -> None:
    print("\n场景 B｜报销驳回重提改道——高额被驳 → 改 900 元重提 → 分支复算走快道")
    iid = engine.runtime.start_process(
        "suite_expense",
        start_user="u_dev_wu",
        variables={"amount": 8000, "expense_type": "office"},
    )
    step(
        engine,
        iid,
        "第 1 步 发起 8000 元（高额道）",
        nodes={"fin_countersign"},
        tasks={("fin_countersign", "u_chief_acc"), ("fin_countersign", "u_cash")},
    )

    reject_of(engine, iid, "u_chief_acc", comment="金额有疑问，退回补充材料")
    print(f"    ✓ 已办：{archived(engine, iid)}")
    step(
        engine,
        iid,
        "第 2 步 孙会计驳回（一票否决 → 回发起人）",
        nodes={"start"},
        tasks={("start", "u_dev_wu")},
    )
    assert any(
        row.cause_token_id and row.outcome == "auto_pass"
        for row in engine.history.task_history(instance_id=iid, assignee="u_cash")
    ), "驳回应连带归档周出纳待办并带 cause_token_id"

    complete_of(engine, iid, "u_dev_wu", variables={"amount": 900}, comment="修改后重报")
    step(
        engine,
        iid,
        "第 3 步 发起人改 900 元重提（路由按新数据复算）",
        nodes={"quick_review"},
        tasks={("quick_review", "u_lead_be")},
    )

    complete_of(engine, iid, "u_lead_be", comment="小额，同意")
    step(engine, iid, "第 4 步 主管王工快审通过 → 完结", nodes=set(), state="completed")
    entered = set(entered_activities(engine, iid))
    assert {"fin_countersign", "start", "amount_route", "quick_review"} <= entered


def scenario_purchase_parallel(engine: ProcessEngine) -> None:
    print("\n场景 C｜采购 50000 高风险——并行分叉 → 合规子流程 → 等齐收口 → 管理员兜底")
    iid = engine.runtime.start_process(
        "suite_purchase",
        start_user="u_buyer",
        variables={"budget": 50000, "risk": "high", "supplier": "华铝"},
    )
    step(
        engine,
        iid,
        "第 1 步 发起后（主管审批）",
        nodes={"dept_review"},
        tasks={("dept_review", "u_purch_head")},
    )

    complete_of(engine, iid, "u_purch_head", comment="同意立项")
    step(
        engine,
        iid,
        "第 2 步 主管通过 → 双条件边同时命中并行",
        nodes={"legal_track", "compliance_track"},
        tasks={("legal_track", "u_ceo")},
    )

    child = next(row.id for row in engine.history.instances() if row.parent_id == iid)
    print(f"    ▶ call_activity 生成子流程实例 {child[:8]}（供应商合规核查）")
    step(
        engine,
        child,
        "第 3 步 子流程落在合规核查",
        nodes={"check"},
        tasks={("check", "u_compliance")},
    )
    complete_of(engine, child, "u_compliance", variables={"compliance": "passed"})
    step(engine, child, "第 4 步 王合规通过 → 子流程完结", nodes=set(), state="completed")
    step(
        engine,
        iid,
        "第 5 步 合规道落定（等齐门等法务道）",
        nodes={"legal_track"},
        tasks={("legal_track", "u_ceo")},
    )

    complete_of(engine, iid, "u_ceo", comment="合同条款无异议")
    step(
        engine,
        iid,
        "第 6 步 法务通过 → 等齐门放行，会签收口两人到齐",
        nodes={"final_sign"},
        tasks={("final_sign", "u_cfo"), ("final_sign", "u_compliance")},
    )

    complete_of(engine, iid, "u_cfo", comment="预算内同意")
    step(
        engine,
        iid,
        "第 7 步 CFO 同意（>=60% 需 2 人，还差一票）",
        nodes={"final_sign"},
        tasks={("final_sign", "u_compliance")},
    )

    complete_of(engine, iid, "u_compliance", comment="合规通过")
    step(
        engine,
        iid,
        "第 8 步 会签 2/2 → 空角色解析走管理员兜底",
        nodes={"admin_gate"},
        tasks={("admin_gate", "u_ceo")},
    )

    complete_of(engine, iid, "u_ceo", comment="管理员放行")
    step(engine, iid, "第 9 步 管理员放行 → 打款完结", nodes=set(), state="completed")
    variables = engine.history.archive_instance(iid)["instance"]["variables"]
    assert variables["compliance"] == "passed", "子流程 output_mappings 应回传"
    assert variables["paid_amount"] == 50000, "打款动作应记录预算金额"
    print("    ✓ 子流程回传 compliance=passed；打款 paid_amount=50000")


def main() -> None:
    print("═" * 72)
    print("flowork 走查演示·顺路场景（每步位置快照 + 硬校验）")
    print("═" * 72)
    with tempfile.TemporaryDirectory() as tmp_name:
        store_engine = make_store_engine(Path(tmp_name))
        scenario_expense_high_lane(store_engine)
        scenario_expense_reject_reroute(store_engine)
    scenario_purchase_parallel(make_static_engine())
    print("\n顺路场景全部通过（后续专场：walkthrough_reject_demo / walkthrough_fault_demo）")


if __name__ == "__main__":
    main()
