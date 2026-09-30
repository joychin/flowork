"""试点场景：报销（金额矩阵 / 会签比例 / 多级驳回 / 退回发起人改金额 / 委托 / 幂等）。"""

from __future__ import annotations

import pytest

from flowork.engine.engine import ProcessEngine
from tests.pilot.conftest import activities_of, complete_of, reject_of


def assignees_of(pilot: ProcessEngine, instance_id: str) -> set[str]:
    return {t.assignee for t in pilot.task.list_tasks(instance_id=instance_id)}


def start_expense(pilot: ProcessEngine, amount: int) -> str:
    return pilot.runtime.start_process(
        "expense_claim", start_user="u_zhang", variables={"amount": amount}
    )


# —— 金额矩阵 ——


@pytest.mark.parametrize("amount", [300, 5000, 20000])
def test_expense_amount_matrix(pilot: ProcessEngine, amount: int):
    """三档金额各走正确路径并完结（含 service 打款与法务传阅）。"""
    instance_id = start_expense(pilot, amount)
    complete_of(pilot, instance_id, "u_dave")  # 主管（u_li 委托 u_dave）

    if amount < 1000:
        assert pilot.task.list_tasks(instance_id=instance_id) == []  # 主管直审即打款完结
        assert pilot.runtime.get_instance(instance_id).state == "completed"
    elif amount < 10000:
        assert assignees_of(pilot, instance_id) == {"u_cai1", "u_cai2"}
        complete_of(pilot, instance_id, "u_cai1")  # 1/2 = 50% 达成
        assert pilot.runtime.get_instance(instance_id).state == "completed"
    else:
        assert activities_of(pilot, instance_id) == [("director_review", "u_chen")]
        complete_of(pilot, instance_id, "u_chen")
        assert assignees_of(pilot, instance_id) == {"u_cai1", "u_cai2"}
        complete_of(pilot, instance_id, "u_cai1")
        assert pilot.runtime.get_instance(instance_id).state == "completed"
        cc = pilot.task.list_cc_tasks(recipient="u_fa1")
        assert len(cc) == 1  # 法务留档传阅
    # 打款动作已执行（service 节点写入 paid 变量）
    from flowork.persistence.orm import ProcessInstanceRow

    variables = pilot.execute(
        lambda ctx: ctx.session.get(ProcessInstanceRow, instance_id).variables
    )
    assert variables.get("paid") is True


def test_expense_supervisor_delegation_to_dave(pilot: ProcessEngine):
    """委托：u_li 的审批由受托人 u_dave 承接。"""
    instance_id = start_expense(pilot, 500)
    assert activities_of(pilot, instance_id) == [("supervisor", "u_dave")]  # 委托生效
    complete_of(pilot, instance_id, "u_dave")
    assert pilot.runtime.get_instance(instance_id).state == "completed"


def test_expense_finance_50_percent_and_auto_pass(pilot: ProcessEngine):
    """中额会签 >=50%：1/2 即达成，另一人记 auto_pass。"""
    instance_id = start_expense(pilot, 5000)
    complete_of(pilot, instance_id, "u_dave")  # 主管（委托）
    complete_of(pilot, instance_id, "u_cai2")  # 第二财务先办
    assert pilot.runtime.get_instance(instance_id).state == "completed"
    outcomes = {(h.assignee, h.outcome) for h in pilot.history.task_history(instance_id)}
    assert ("u_cai1", "auto_pass") in outcomes  # 剩余待办自动通过
    assert ("u_cai2", "agree") in outcomes


def test_expense_finance_reject_back_to_supervisor_then_pass(pilot: ProcessEngine):
    """财务驳回 → 主管重办 → 重提通过（多级驳回链第一环）。"""
    instance_id = start_expense(pilot, 5000)
    complete_of(pilot, instance_id, "u_dave")
    reject_of(pilot, instance_id, "u_cai1", comment="发票不合规")
    assert activities_of(pilot, instance_id) == [("supervisor", "u_dave")]  # 回主管
    complete_of(pilot, instance_id, "u_dave", comment="已换发票")
    # 复活后两人待办都在；1/2=50% 出口：cai1 办完即达成，cai2 记 auto_pass
    assert assignees_of(pilot, instance_id) == {"u_cai1", "u_cai2"}
    complete_of(pilot, instance_id, "u_cai1")
    assert pilot.runtime.get_instance(instance_id).state == "completed"
    # 历史：驳回记录 + 重办记录共存（append-only）
    outcomes = [(h.activity_code, h.outcome) for h in pilot.history.task_history(instance_id)]
    assert outcomes.count(("finance_review", "reject")) == 1
    assert outcomes.count(("supervisor", "agree")) == 2
    assert ("finance_review", "auto_pass") in outcomes


def test_expense_supervisor_reject_to_originator_then_change_amount(pilot: ProcessEngine):
    """退回发起人：主管驳回到 start → 发起人重报（改金额）→ 分流路径随之改变。

    20000（大额）被驳回后改为 500（小额）：重报后主管直审即完结，
    不再进入总监/财务环节——变量驱动路由复算。
    """
    instance_id = start_expense(pilot, 20000)
    reject_of(pilot, instance_id, "u_dave", comment="预算超了，改小额")
    assert activities_of(pilot, instance_id) == [("start", "u_zhang")]  # 发起人重报任务

    # 发起人修改金额后重新提交（走 start 任务）
    task = pilot.task.list_tasks(instance_id=instance_id, assignee="u_zhang")[0]
    pilot.task.complete(task.id, actor="u_zhang", variables={"amount": 500})

    assert activities_of(pilot, instance_id) == [("supervisor", "u_dave")]  # 重新从主管走
    complete_of(pilot, instance_id, "u_dave")
    assert pilot.runtime.get_instance(instance_id).state == "completed"
    # 大额分支节点从未被激活
    tokens = {t.activity_code for t in pilot.history.tokens(instance_id)}
    assert "director_review" not in tokens and "finance_review" not in tokens


def test_expense_idempotent_complete_in_countersign(pilot: ProcessEngine):
    """中额会签幂等：同键重复提交不产生第二次推进。"""
    instance_id = start_expense(pilot, 5000)
    complete_of(pilot, instance_id, "u_dave")
    tasks = pilot.task.list_tasks(instance_id=instance_id, assignee="u_cai1")
    first = pilot.task.complete(tasks[0].id, actor="u_cai1", idempotency_key="pay-001")
    again = pilot.task.complete(tasks[0].id, actor="u_cai1", idempotency_key="pay-001")
    assert first.outcome == again.outcome == "agree"
    assert again.instance_state == "completed"
    agrees = [
        h
        for h in pilot.history.task_history(instance_id)
        if h.activity_code == "finance_review" and h.outcome == "agree"
    ]
    assert len(agrees) == 1  # 只有第一条真实提交


def test_expense_add_countersigner_midway(pilot: ProcessEngine):
    """会签中途加签：item_count 联动，三人 >=50% 需 2 票。"""
    instance_id = start_expense(pilot, 5000)
    complete_of(pilot, instance_id, "u_dave")
    cai1 = pilot.task.list_tasks(instance_id=instance_id, assignee="u_cai1")[0]
    pilot.task.add_countersigner(cai1.id, users=["u_cai2", "u_hr1"], actor="u_cai1")
    # 现在 3 人（cai1, cai2, hr1），>=50% 需 2 票
    complete_of(pilot, instance_id, "u_cai1")
    assert pilot.runtime.get_instance(instance_id).state == "running"  # 1/3 不足
    complete_of(pilot, instance_id, "u_hr1")
    assert pilot.runtime.get_instance(instance_id).state == "completed"  # 2/3 达 50%（ceil 1.5→2）
    outcomes = {(h.assignee, h.outcome) for h in pilot.history.task_history(instance_id)}
    assert ("u_cai2", "auto_pass") in outcomes
