"""M3 语义测试：任务操作全集（认领/转办/协办/传阅/加签/撤回/管理员操作/实例终止）。"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.definitions.node import MultiInstance
from flowork.engine.engine import ProcessEngine
from flowork.errors import InstanceNotFoundError, TaskNotAvailableError


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def publish(engine: ProcessEngine, definition) -> None:
    engine.repository.deploy(definition)


def linear_flow(code: str = "ops") -> FlowBuilder:
    """a(直派) → b(候选) → c(会签) → 传阅 → 结束。"""
    return (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", candidate_users=["'u_c1'", "'u_c2'"])
        .user_task(
            "c", assignees=["'u_p1'", "'u_p2'"], multi_instance=MultiInstance(mode="parallel")
        )
        .cc_task("notify", recipients=["'u_a'"])
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "c")
        .connect("c", "notify")
        .connect("notify", "end")
    )


def start(engine: ProcessEngine, code: str = "ops") -> str:
    publish(engine, linear_flow(code).build())
    return engine.runtime.start_process(code, start_user="u_starter")


def complete_of(engine: ProcessEngine, instance_id: str, assignee: str) -> object:
    task = engine.task.list_tasks(instance_id=instance_id, assignee=assignee)[0]
    return engine.task.complete(task.id, actor=assignee)


# —— 认领（claim/unclaim）——


def test_claim_by_candidate_assigns_task(engine: ProcessEngine):
    instance_id = start(engine)
    complete_of(engine, instance_id, "u_a")
    claimable = engine.task.list_tasks(instance_id=instance_id)[0]
    assert claimable.assignee == ""  # 候选任务未认领

    engine.task.claim(claimable.id, "u_c1")
    claimed = engine.task.get_task(claimable.id)
    assert claimed is not None and claimed.assignee == "u_c1"


def test_claim_by_non_candidate_rejected(engine: ProcessEngine):
    instance_id = start(engine)
    complete_of(engine, instance_id, "u_a")
    claimable = engine.task.list_tasks(instance_id=instance_id)[0]
    with pytest.raises(TaskNotAvailableError):
        engine.task.claim(claimable.id, "u_outsider")


def test_complete_claimable_without_claim_rejected(engine: ProcessEngine):
    instance_id = start(engine)
    complete_of(engine, instance_id, "u_a")
    claimable = engine.task.list_tasks(instance_id=instance_id)[0]
    with pytest.raises(TaskNotAvailableError):
        engine.task.complete(claimable.id, actor="u_c1")  # 必须先认领


def test_unclaim_returns_to_pool(engine: ProcessEngine):
    instance_id = start(engine)
    complete_of(engine, instance_id, "u_a")
    claimable = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.claim(claimable.id, "u_c2")
    engine.task.unclaim(claimable.id, "u_c2")
    assert engine.task.get_task(claimable.id).assignee == ""


def test_unclaim_by_non_owner_rejected(engine: ProcessEngine):
    instance_id = start(engine)
    complete_of(engine, instance_id, "u_a")
    claimable = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.claim(claimable.id, "u_c1")
    with pytest.raises(TaskNotAvailableError):
        engine.task.unclaim(claimable.id, "u_c2")


# —— 转办 ——


def test_transfer_reassigns_task(engine: ProcessEngine):
    instance_id = start(engine)
    a_task = engine.task.list_tasks(instance_id=instance_id)[0]
    moved = engine.task.transfer(a_task.id, to_user="u_delegate", actor="u_a", comment="出差")
    assert moved.assignee == "u_delegate"
    assert engine.task.list_tasks(instance_id=instance_id)[0].id == moved.id
    outcomes = [(h.activity_code, h.outcome) for h in engine.history.task_history(instance_id)]
    assert ("a", "transfer") in outcomes


def test_transfer_then_complete_advances(engine: ProcessEngine):
    instance_id = start(engine)
    a_task = engine.task.list_tasks(instance_id=instance_id)[0]
    moved = engine.task.transfer(a_task.id, "u_delegate", actor="u_a")
    engine.task.complete(moved.id, actor="u_delegate")
    assert engine.runtime.get_instance(instance_id).state == "running"
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["b"]


# —— 协办 ——


def test_assist_creates_independent_tasks(engine: ProcessEngine):
    instance_id = start(engine)
    a_task = engine.task.list_tasks(instance_id=instance_id)[0]
    assists = engine.task.assist(a_task.id, users=["u_helper1", "u_helper2"], actor="u_a")
    assert [t.task_type for t in assists] == ["assist", "assist"]

    engine.task.complete_assist(assists[0].id, actor="u_helper1")
    remaining = engine.task.list_tasks(instance_id=instance_id, state="ready")
    assert len([t for t in remaining if t.task_type == "assist"]) == 1
    # 协办不影响主任务
    assert engine.task.get_task(a_task.id) is not None


def test_assist_task_cannot_complete_via_complete(engine: ProcessEngine):
    instance_id = start(engine)
    a_task = engine.task.list_tasks(instance_id=instance_id)[0]
    assists = engine.task.assist(a_task.id, ["u_helper"], actor="u_a")
    with pytest.raises(TaskNotAvailableError):
        engine.task.complete(assists[0].id, actor="u_helper")


# —— 传阅 ——


def test_cc_items_created_and_marked_read(engine: ProcessEngine):
    instance_id = start(engine)
    complete_of(engine, instance_id, "u_a")
    claimable = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.claim(claimable.id, "u_c1")
    engine.task.complete(claimable.id, actor="u_c1")
    for assignee in ("u_p1", "u_p2"):
        complete_of(engine, instance_id, assignee)
    assert engine.runtime.get_instance(instance_id).state == "completed"

    cc = engine.task.list_cc_tasks(recipient="u_a")
    assert len(cc) == 1 and cc[0].is_read is False
    engine.task.mark_cc_read(cc[0].id, user="u_a")
    assert engine.task.list_cc_tasks(recipient="u_a")[0].is_read is True


def test_cc_read_by_other_user_rejected(engine: ProcessEngine):
    instance_id = start(engine)
    complete_of(engine, instance_id, "u_a")
    claimable = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.claim(claimable.id, "u_c1")
    engine.task.complete(claimable.id, actor="u_c1")
    for assignee in ("u_p1", "u_p2"):
        complete_of(engine, instance_id, assignee)
    cc = engine.task.list_cc_tasks(recipient="u_a")[0]
    with pytest.raises(TaskNotAvailableError):
        engine.task.mark_cc_read(cc.id, user="u_other")


# —— 加签 ——


def test_add_countersigner_extends_pool(engine: ProcessEngine):
    instance_id = start(engine)
    complete_of(engine, instance_id, "u_a")
    claimable = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.claim(claimable.id, "u_c1")
    engine.task.complete(claimable.id, actor="u_c1")

    p1 = engine.task.list_tasks(instance_id=instance_id, assignee="u_p1")[0]
    engine.task.add_countersigner(p1.id, users=["u_extra"], actor="u_p1")

    # 3 人并签 all：两票后仍运行
    for assignee in ("u_p1", "u_p2"):
        complete_of(engine, instance_id, assignee)
    assert engine.runtime.get_instance(instance_id).state == "running"
    complete_of(engine, instance_id, "u_extra")
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_add_countersigner_on_finished_task_rejected(engine: ProcessEngine):
    instance_id = start(engine)
    a_task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(a_task.id, actor="u_a")
    with pytest.raises(TaskNotAvailableError):
        engine.task.add_countersigner(a_task.id, ["u_x"], actor="u_a")


# —— 撤回 ——


def test_withdraw_revives_task_and_cancels_downstream(engine: ProcessEngine):
    instance_id = start(engine)
    a_task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(a_task.id, actor="u_a")
    # b 已激活（候选任务）
    revived = engine.task.withdraw(a_task.id, actor="u_a")
    assert (revived.activity_code, revived.assignee) == ("a", "u_a")
    # 下游候选任务被取消
    remaining = [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)]
    assert remaining == ["a"]
    assert engine.runtime.get_instance(instance_id).state == "running"


def test_withdraw_by_non_handler_rejected(engine: ProcessEngine):
    instance_id = start(engine)
    a_task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(a_task.id, actor="u_a")
    with pytest.raises(TaskNotAvailableError):
        engine.task.withdraw(a_task.id, actor="u_other")


# —— 管理员操作 ——


def test_admin_activate_node(engine: ProcessEngine):
    instance_id = start(engine)
    token_id = engine.task.activate_node(instance_id, "c", actor="admin", participants=["u_x"])
    assert token_id
    tasks = [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)]
    assert "c" in tasks


def test_admin_cancel_node(engine: ProcessEngine):
    instance_id = start(engine)
    engine.task.cancel_node(instance_id, "a", actor="admin")
    assert engine.task.list_tasks(instance_id=instance_id) == []
    token = next(t for t in engine.history.tokens(instance_id) if t.activity_code == "a")
    assert token.state == "cancelled"


def test_admin_finish_instance(engine: ProcessEngine):
    instance_id = start(engine)
    engine.task.finish_instance(instance_id, actor="admin")
    assert engine.runtime.get_instance(instance_id).state == "completed"
    assert engine.task.list_tasks(instance_id=instance_id) == []


def test_admin_remove_instance(engine: ProcessEngine):
    instance_id = start(engine)
    engine.task.remove_instance(instance_id, actor="admin")
    with pytest.raises(InstanceNotFoundError):
        engine.runtime.get_instance(instance_id)
    # 未投递事件保留（2026-10-13 事件 review 复核批）：删除实例不再连坐
    # 未消费的事件流——实例没了，已发生的事还在（订阅方仍可消费）
    assert engine.history.events(instance_id)


def test_cancel_process_via_runtime(engine: ProcessEngine):
    instance_id = start(engine)
    engine.runtime.cancel_process(instance_id, actor="u_starter", reason="不需要了")
    assert engine.runtime.get_instance(instance_id).state == "cancelled"
    assert engine.task.list_tasks(instance_id=instance_id) == []


# —— 边界 ——


def test_operate_on_missing_task_raises(engine: ProcessEngine):
    with pytest.raises(TaskNotAvailableError):
        engine.task.claim("no-such-task", "u_x")


def test_transfer_missing_task_raises(engine: ProcessEngine):
    with pytest.raises(TaskNotAvailableError):
        engine.task.transfer("no-such-task", "u_x", actor="u_x")


def test_withdraw_rejected_task_rejected(engine: ProcessEngine):
    """被驳回的任务（outcome=reject）不可撤回。"""
    definition = (
        FlowBuilder("wd")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("wd", start_user="u_s")
    a_task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(a_task.id, actor="u_a")
    b_task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.reject(b_task.id, actor="u_b")
    with pytest.raises(TaskNotAvailableError):
        engine.task.withdraw(b_task.id, actor="u_b")


def test_claim_events_emitted(engine: ProcessEngine):
    instance_id = start(engine)
    complete_of(engine, instance_id, "u_a")
    claimable = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.claim(claimable.id, "u_c1")
    names = [e.event_name for e in engine.history.events(instance_id)]
    assert "task_claimed" in names


def test_cc_recipients_cel_initiator_and_function(engine: ProcessEngine):
    """传阅位 CEL（spec §4.2 位点统一）：initiator 标识符与取人函数混排。"""
    from flowork import contrib
    from flowork.engine.config import EngineConfig
    from flowork.participants.org_directory import OrgDirectory

    org = OrgDirectory.from_dict(
        {
            "users": {
                "u_s": {"manager": "u_boss", "roles": ["finance"]},
                "u_boss": {"roles": []},
                "u_fin": {"roles": ["finance"]},
            },
        }
    )
    configured = ProcessEngine(
        EngineConfig(org=org, expression_functions=contrib.cel_vocabulary(org))
    )
    flow = (
        FlowBuilder("cc_cel")
        .start()
        .cc_task("notify", recipients=["initiator", "manager_of(initiator)"])
        .end()
        .connect("start", "notify")
        .connect("notify", "end")
    )
    publish(configured, flow.build())
    instance_id = configured.runtime.start_process("cc_cel", start_user="u_s")
    recipients = sorted(
        cc.recipient for cc in configured.task.list_cc_tasks(instance_id=instance_id)
    )
    assert recipients == ["u_boss", "u_s"]


def test_cc_recipients_formula_error_is_technical_fault():
    """传阅公式求值错 → 挂起 + 故障单（错误二分，spec §8）。"""
    from flowork.engine.kernel import EngineExecutionError

    flow = (
        FlowBuilder("cc_bad")
        .start()
        .cc_task("notify", recipients=["nosuchvar"])
        .end()
        .connect("start", "notify")
        .connect("notify", "end")
    )
    engine = ProcessEngine()
    publish(
        engine,
        flow.build().model_copy(
            update={"variables": [ProcessVariable(name="nosuchvar", type="number")]}
        ),
    )
    with pytest.raises(EngineExecutionError, match="传阅接收人公式求值失败"):
        engine.runtime.start_process("cc_bad", start_user="u_s")


def test_add_countersigner_duplicate_rejected_wholesale(engine: ProcessEngine):
    """回归（code-review 2026-10 P1-1）：对已在会签名单的人加签＝整笔拒绝。
    修复前重复成员被克隆成第二张任务——一人两票，出口提前达成、其余成员
    被 auto_pass 从未参与（违背「加签人各占一票」）。"""
    instance_id = start(engine)
    complete_of(engine, instance_id, "u_a")
    claimable = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.claim(claimable.id, "u_c1")
    engine.task.complete(claimable.id, actor="u_c1")
    p1 = engine.task.list_tasks(instance_id=instance_id, assignee="u_p1")[0]

    with pytest.raises(TaskNotAvailableError, match="已在会签名单"):
        engine.task.add_countersigner(p1.id, users=["u_extra", "u_p2"], actor="u_p1")
    # 整笔回滚：请求里的新人 u_extra 也没被加入
    assert sorted(t.assignee for t in engine.task.list_tasks(instance_id=instance_id)) == [
        "u_p1",
        "u_p2",
    ]

    # 新人加签不受影响
    engine.task.add_countersigner(p1.id, users=["u_extra"], actor="u_p1")
    assert "u_extra" in [t.assignee for t in engine.task.list_tasks(instance_id=instance_id)]


# —— code-review 2026-10 P1-5 / P1-16 ——


def test_reject_revival_of_countersigned_node_succeeds(engine: ProcessEngine):
    """回归（P1-5）：加签过的未声明 multi_instance 节点被驳回复活不再挂起。
    manual 名单＝历史定格（加签扩员/复活圈定），不适用「单人节点多人＝错误」
    守卫——修复前复活必然 ParticipantCountMismatchError。"""
    flow = (
        FlowBuilder("p15")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
    )
    publish(engine, flow.build())
    instance_id = engine.runtime.start_process("p15", start_user="u_s")
    a_task = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    engine.task.add_countersigner(a_task.id, users=["u_a2"], actor="u_a")
    engine.task.complete(
        engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0].id, actor="u_a"
    )
    engine.task.complete(
        engine.task.list_tasks(instance_id=instance_id, assignee="u_a2")[0].id, actor="u_a2"
    )
    b_task = engine.task.list_tasks(instance_id=instance_id, assignee="u_b")[0]
    engine.task.reject(b_task.id, actor="u_b", target="a")  # 驳回到 a：复活两名办理人
    revived = engine.task.list_tasks(instance_id=instance_id)
    assert sorted(t.assignee for t in revived) == ["u_a", "u_a2"], "复活名单完整"


def test_assist_and_consult_respect_max_participants():
    """回归（P1-16）：assist/consult 与加签同守规模护栏——同根在办＋请求
    超 max_participants 整笔拒绝（此前不设防可无限开任务）。"""
    from flowork.api import EngineConfig

    bounded = ProcessEngine(EngineConfig(max_participants=2))
    instance_id = start(bounded)
    complete_of(bounded, instance_id, "u_a")
    claimable = bounded.task.list_tasks(instance_id=instance_id)[0]
    bounded.task.claim(claimable.id, "u_c1")
    bounded.task.complete(claimable.id, actor="u_c1")
    p1 = bounded.task.list_tasks(instance_id=instance_id, assignee="u_p1")[0]

    from flowork.errors import TaskNotAvailableError

    created = bounded.task.assist(p1.id, users=["u_x1"], actor="u_p1")  # 累计 1 ≤ 2
    assert [t.task_type for t in created] == ["assist"]
    with pytest.raises(TaskNotAvailableError, match="超过上限"):
        bounded.task.assist(p1.id, users=["u_x2", "u_x3"], actor="u_p1")  # 1+2 > 2

    # 咨询软挂起使主任务二次 consult 被拦——limit=1 引擎单任务一次请求即触顶
    bounded2 = ProcessEngine(EngineConfig(max_participants=1))
    bounded2.repository.deploy(
        FlowBuilder("ops2")
        .start()
        .user_task("a", assignee="'u_m'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    instance_id2 = bounded2.runtime.start_process("ops2", start_user="u_s")
    p1b = bounded2.task.list_tasks(instance_id=instance_id2, assignee="u_m")[0]
    with pytest.raises(TaskNotAvailableError, match="超过上限"):
        bounded2.task.consult(p1b.id, users=["u_y1", "u_y2"], actor="u_m")


def test_remove_instance_sweeps_v2_tables_and_refuses_unsettled_children(engine: ProcessEngine):
    """回归（code-review 2026-10 P1-13）：remove_instance 同删 v2 四表
    （变量索引/变量历史/路由审计/消息订阅——此前残留永久孤儿行）；
    有未结算子实例时拒绝（先 terminate 级联），不再留下继续运行的孤儿。"""
    from sqlalchemy import select

    from flowork.errors import InstanceNotFoundError
    from flowork.persistence.orm import ProcessInstanceRow
    from flowork.persistence.orm_v2 import RouteDecisionRow, VariableHistoryRow

    child = (
        FlowBuilder("p13_child")
        .start()
        .user_task("work", assignee="'u_w'")
        .end()
        .connect("start", "work")
        .connect("work", "end")
        .build()
    )
    publish(engine, child)
    parent = (
        FlowBuilder("p13_parent")
        .start()
        .call_activity("sub", process_code="p13_child")
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    publish(engine, parent)
    instance_id = engine.runtime.start_process("p13_parent", start_user="u_s")

    with pytest.raises(InstanceNotFoundError, match="未结算子实例"):
        engine.task.remove_instance(instance_id, actor="u_admin")

    engine.runtime.terminate_process(instance_id, actor="u_admin")  # 级联终止后可删
    engine.task.remove_instance(instance_id, actor="u_admin")
    with pytest.raises(InstanceNotFoundError):
        engine.runtime.get_instance(instance_id)  # 已删除
    with engine.session_factory() as session:
        for row_type in (RouteDecisionRow, VariableHistoryRow):
            leftovers = session.scalars(
                select(row_type).where(row_type.instance_id == instance_id)
            ).all()
            assert leftovers == [], f"{row_type.__name__} 残留"
        orphans = session.scalars(
            select(ProcessInstanceRow).where(ProcessInstanceRow.parent_id == instance_id)
        ).all()
        assert orphans == [], "子实例行残留"


def test_transfer_keeps_priority(engine: ProcessEngine):
    """回归（code-review 2026-10 P2-8）：转办克隆携带 priority——修复前回落
    默认 50（新 TaskRow 不复制）。"""

    from flowork.persistence.orm import TaskRow

    instance_id = start(engine)
    task = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    with engine.session_factory() as session:
        row = session.get(TaskRow, task.id)
        assert row is not None
        row.priority = 80
        session.commit()
    moved = engine.task.transfer(task.id, "u_backup", actor="u_a")
    assert moved.priority == 80, "转办后优先级保持"


def test_remove_instance_keeps_undelivered_events_and_sweeps_descendants(
    engine: ProcessEngine,
):
    """回归（2026-10-13 事件 review 复核批）：remove_instance 修前整删 outbox
    含未投递行（订阅方丢事件、审计链断裂），且只按根 id 删运行时数据——
    已结算子孙的任务/令牌/事件残留孤儿行。修后未投递行保留、全树同删。"""
    from sqlalchemy import select

    from flowork.persistence.orm import EventOutboxRow, TaskHistoryRow, TokenRow

    child = (
        FlowBuilder("rm2_child")
        .start()
        .user_task("work", assignee="'u_w'")
        .end()
        .connect("start", "work")
        .connect("work", "end")
        .build()
    )
    publish(engine, child)
    parent = (
        FlowBuilder("rm2_parent")
        .start()
        .call_activity("sub", process_code="rm2_child")
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    publish(engine, parent)
    instance_id = engine.runtime.start_process("rm2_parent", start_user="u_s")

    child_row = next(i for i in engine.history.instances() if i.parent_id == instance_id)
    work = engine.task.list_tasks(instance_id=child_row.id)[0]
    engine.task.complete(work.id, actor="u_w")  # 子结算（父随之完成，全树事件未消费）

    engine.task.remove_instance(instance_id, actor="u_admin")
    with engine.session_factory() as session:
        # 未投递事件保留待消费（instance_id 悬空是删除实例的既定代价）
        kept = session.scalars(
            select(EventOutboxRow).where(EventOutboxRow.instance_id == instance_id)
        ).all()
        assert kept, "未投递事件不应随实例删除"
        assert all(row.delivered_at is None for row in kept)
        # 子孙运行时数据整树同删（修前按根 id 残留孤儿）
        child_ids = {child_row.id}
        assert (
            session.scalars(select(TokenRow).where(TokenRow.instance_id.in_(child_ids))).all() == []
        )
        assert (
            session.scalars(
                select(TaskHistoryRow).where(TaskHistoryRow.instance_id.in_(child_ids))
            ).all()
            == []
        )
