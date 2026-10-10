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
    assert engine.history.events(instance_id) == []


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
