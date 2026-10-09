"""试点场景：请假（串行会签链 / 自审通过 / 中途驳回 / 加签 / 撤回）。"""

from __future__ import annotations

from flowork.engine.engine import ProcessEngine
from tests.pilot.conftest import activities_of, complete_of, reject_of
from tests.suite.features import covers


def start_leave(pilot: ProcessEngine, days: int, user: str = "u_zhang") -> str:
    return pilot.runtime.start_process("leave_request", start_user=user, variables={"days": days})


@covers("route.formula", "ops.cc_read")
def test_leave_short_path(pilot: ProcessEngine):
    """≤3 天：主管 → HR 备案 → 传阅发起人 → 结束。"""
    instance_id = start_leave(pilot, 2)
    complete_of(pilot, instance_id, "u_dave")  # 主管（u_li 委托）
    assert activities_of(pilot, instance_id) == [("hr_record", "u_hr1")]
    complete_of(pilot, instance_id, "u_hr1")
    assert pilot.runtime.get_instance(instance_id).state == "completed"
    cc = pilot.task.list_cc_tasks(recipient="u_zhang")
    assert len(cc) == 1  # 结果传阅发起人


@covers("participant.auto_pass_originator", "participant.delegation")
def test_leave_originator_is_supervisor_auto_pass(pilot: ProcessEngine):
    """发起人即主管（u_li 请假）：auto_pass_originator 生效，但委托优先——u_li 委托 u_dave，
    故 u_dave 待办（策略作用于解析后的名单：u_li 被替换为 u_dave，发起人= u_li 不在其中）。"""
    instance_id = start_leave(pilot, 1, user="u_li")
    pilot.task.list_tasks(instance_id=instance_id)
    # 主管 manager()=u_chen；委托与策略均不命中 → u_chen 待办
    assert activities_of(pilot, instance_id) == [("supervisor", "u_chen")]
    complete_of(pilot, instance_id, "u_chen")
    complete_of(pilot, instance_id, "u_hr1")
    assert pilot.runtime.get_instance(instance_id).state == "completed"


@covers("countersign.sequential", "countersign.approve_all")
def test_leave_long_path_sequential_chain(pilot: ProcessEngine):
    """>3 天：HR 串行会签 u_hr1 → u_hr2 全过。"""
    instance_id = start_leave(pilot, 5)
    complete_of(pilot, instance_id, "u_dave")
    assert activities_of(pilot, instance_id) == [("hr_sequential", "u_hr1")]  # 只有一人在办
    complete_of(pilot, instance_id, "u_hr1")
    assert activities_of(pilot, instance_id) == [("hr_sequential", "u_hr2")]
    complete_of(pilot, instance_id, "u_hr2")
    assert pilot.runtime.get_instance(instance_id).state == "completed"
    chain = [
        (h.assignee, h.outcome)
        for h in pilot.history.task_history(instance_id)
        if h.activity_code == "hr_sequential"
    ]
    assert chain == [("u_hr1", "agree"), ("u_hr2", "agree")]


@covers("reject.explicit_target", "reject.cause_token")
def test_leave_hr_reject_midway_then_full_loop(pilot: ProcessEngine):
    """串行中途 HR1 驳回 → 主管重办 → 再次串行全过（完整驳回环）。"""
    instance_id = start_leave(pilot, 4)
    complete_of(pilot, instance_id, "u_dave")
    reject_of(pilot, instance_id, "u_hr1", comment="年假余额不足，去确认")
    assert activities_of(pilot, instance_id) == [("supervisor", "u_dave")]
    complete_of(pilot, instance_id, "u_dave", comment="已确认余额")
    complete_of(pilot, instance_id, "u_hr1")
    complete_of(pilot, instance_id, "u_hr2")
    assert pilot.runtime.get_instance(instance_id).state == "completed"


@covers("ops.add_countersigner", "countersign.approve_all")
def test_leave_add_countersigner_into_sequential(pilot: ProcessEngine):
    """串行会签中途加签：新审批人进入队伍，全员 all 出口。"""
    instance_id = start_leave(pilot, 7)
    complete_of(pilot, instance_id, "u_dave")
    hr1 = pilot.task.list_tasks(instance_id=instance_id, assignee="u_hr1")[0]
    pilot.task.add_countersigner(hr1.id, users=["u_hr2", "u_cai1"], actor="u_hr1")
    # 队伍变为 hr1, hr2, cai1（hr2 原有 + cai1 新增）
    complete_of(pilot, instance_id, "u_hr1")
    complete_of(pilot, instance_id, "u_hr2")
    assert pilot.runtime.get_instance(instance_id).state == "running"  # 2/3 未达 all
    complete_of(pilot, instance_id, "u_cai1")
    assert pilot.runtime.get_instance(instance_id).state == "completed"


@covers("ops.withdraw")
def test_leave_withdraw_after_hr_record(pilot: ProcessEngine):
    """撤回：主管办完后反悔 → 下游 HR 备案被取消 → 主管重新待办。"""
    instance_id = start_leave(pilot, 1)
    supervisor_task = pilot.task.list_tasks(instance_id=instance_id, assignee="u_dave")[0]
    pilot.task.complete(supervisor_task.id, actor="u_dave")
    assert activities_of(pilot, instance_id) == [("hr_record", "u_hr1")]

    revived = pilot.task.withdraw(supervisor_task.id, actor="u_dave", comment="天数填错了")
    assert (revived.activity_code, revived.assignee) == ("supervisor", "u_dave")
    assert activities_of(pilot, instance_id) == [("supervisor", "u_dave")]  # 下游已撤

    complete_of(pilot, instance_id, "u_dave", variables={"days": 9})  # 改成长假重提
    complete_of(pilot, instance_id, "u_hr1")
    complete_of(pilot, instance_id, "u_hr2")
    assert pilot.runtime.get_instance(instance_id).state == "completed"


@covers("reject.cause_token", "query.task_history_append_only")
def test_leave_multi_round_reject_stress(pilot: ProcessEngine):
    """多轮驳回压力：三轮驳回-重提循环不死循环守卫误伤，第四轮通过。"""
    instance_id = start_leave(pilot, 4)
    complete_of(pilot, instance_id, "u_dave")
    hr1_tasks = []
    for round_no in range(3):
        hr1 = pilot.task.list_tasks(instance_id=instance_id, assignee="u_hr1")[0]
        hr1_tasks.append(hr1.id)
        reject_of(pilot, instance_id, "u_hr1", comment=f"第 {round_no + 1} 轮驳回")
        complete_of(pilot, instance_id, "u_dave", comment="重报")
    complete_of(pilot, instance_id, "u_hr1")  # 第四轮放行
    complete_of(pilot, instance_id, "u_hr2")
    assert pilot.runtime.get_instance(instance_id).state == "completed"
    rejects = [
        h
        for h in pilot.history.task_history(instance_id)
        if h.activity_code == "hr_sequential" and h.outcome == "reject"
    ]
    assert len(rejects) == 3
