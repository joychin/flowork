"""行业对标第一批语义测试：A3 复活最近一轮 / A4 root 链与协办收口 / A6 参与者上限。

依据 spec v2.3 第八轮拍板（docs/product/kernel-v2/对标明细详设.md）：
- A3：驳回复活名单＝目标节点**最近一个已完结令牌**名下的办理人（多轮驳回不膨胀）；
  与主流「重入重新解析」的刻意差异＝复活原办理人。
- A4：assist/加签任务的 root_task_id 指回主任务（链完整）；协办不占票且随主任务
  终态（complete/reject）同事务收口，outcome=assist_cancel。
- A6：参与者超限＝ParticipantLimitError（技术故障→挂起+故障单），**绝不静默截断**。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.node import MultiInstance
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.errors import TaskNotAvailableError
from flowork.participants.org_directory import Delegation, OrgDirectory
from flowork.persistence.orm import TaskRow


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _history(engine: ProcessEngine, instance_id: str) -> list[tuple[str, str, str]]:
    return [
        (h.activity_code, h.assignee, h.outcome) for h in engine.history.task_history(instance_id)
    ]


def _task_row(engine: ProcessEngine, task_id: str) -> TaskRow:
    with engine.session_factory() as session:
        row = session.get(TaskRow, task_id)
        assert row is not None
        # 脱离会话后仍可读已加载的普通列
        return row


# —— A3：驳回复活圈定最近一轮 ——


def test_reject_revival_scopes_to_latest_round():
    """三轮驳回：复活名单只取最近一轮的办理人（委托中途介入使两轮人员不同）。"""
    org = OrgDirectory()
    engine = ProcessEngine(EngineConfig(org=org))
    flow = (
        FlowBuilder("r3")
        .start()
        .user_task("a", assignee="'u_bob'")
        .user_task("b", assignee="'u_carol'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("r3", start_user="u_starter")

    # 第一轮：u_bob 办 a；引入委托 u_bob→u_dave 后驳回回 a——复活时替换生效
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_bob")[0]
    engine.task.complete(task_a.id, actor="u_bob")
    task_b = engine.task.list_tasks(instance_id=instance_id, assignee="u_carol")[0]
    org.delegations.append(Delegation(delegate_from="u_bob", delegate_to="u_dave"))
    engine.task.reject(task_b.id, actor="u_carol", target="a")
    assert [t.assignee for t in engine.task.list_tasks(instance_id=instance_id)] == ["u_dave"]

    # 第二轮：u_dave 办 a（最近一轮的办理人）
    task_a2 = engine.task.list_tasks(instance_id=instance_id, assignee="u_dave")[0]
    engine.task.complete(task_a2.id, actor="u_dave")
    task_b2 = engine.task.list_tasks(instance_id=instance_id, assignee="u_carol")[0]

    # 撤掉委托后再次驳回：复活名单应＝最近一轮（u_dave），而非全量历史（u_bob+u_dave）
    org.delegations.clear()
    engine.task.reject(task_b2.id, actor="u_carol", target="a")
    revived = engine.task.list_tasks(instance_id=instance_id)
    assert [t.assignee for t in revived] == ["u_dave"]
    # 历史里两轮办理人都在（append-only），但复活名单不再随轮次膨胀
    assert ("a", "u_bob", "agree") in _history(engine, instance_id)
    assert ("a", "u_dave", "agree") in _history(engine, instance_id)


# —— A4：root 链 + 协办随主任务收口 ——


def _linear(engine: ProcessEngine, code: str = "b1") -> str:
    flow = (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
    )
    engine.repository.deploy(flow.build())
    return engine.runtime.start_process(code, start_user="u_starter")


def test_assist_chain_records_root_task(engine: ProcessEngine):
    instance_id = _linear(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    assists = engine.task.assist(task_a.id, ["u_helper"], actor="u_a")
    assert _task_row(engine, assists[0].id).root_task_id == task_a.id


def test_add_countersigner_chain_records_root_task(engine: ProcessEngine):
    instance_id = _linear(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    created = engine.task.add_countersigner(task_a.id, ["u_extra"], actor="u_a")
    assert _task_row(engine, created[0].id).root_task_id == task_a.id


def test_assist_cancelled_when_main_task_completes(engine: ProcessEngine):
    instance_id = _linear(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.assist(task_a.id, ["u_helper"], actor="u_a")
    engine.task.complete(task_a.id, actor="u_a")

    ready = engine.task.list_tasks(instance_id=instance_id, state="ready")
    assert all(t.task_type != "assist" for t in ready)
    assert ("a", "u_helper", "assist_cancel") in _history(engine, instance_id)
    # 主任务照常推进，流程不卡
    assert [t.activity_code for t in ready] == ["b"]


def test_assist_cancelled_when_main_task_rejected(engine: ProcessEngine):
    instance_id = _linear(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.assist(task_a.id, ["u_helper"], actor="u_a")
    engine.task.reject(task_a.id, actor="u_a")  # 驳回回 start 重报

    ready = engine.task.list_tasks(instance_id=instance_id, state="ready")
    assert all(t.task_type != "assist" for t in ready)
    assert ("a", "u_helper", "assist_cancel") in _history(engine, instance_id)


def test_assist_survives_main_task_transfer(engine: ProcessEngine):
    """转办不终结等待——协办随新办理人继续挂（收口只挂在 complete/reject）。"""
    instance_id = _linear(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.assist(task_a.id, ["u_helper"], actor="u_a")
    moved = engine.task.transfer(task_a.id, to_user="u_b2", actor="u_a")
    assert engine.task.get_task(moved.id) is not None
    ready = engine.task.list_tasks(instance_id=instance_id, state="ready")
    assert any(t.task_type == "assist" for t in ready)


# —— A6：参与者上限护栏（绝不截断） ——


def test_user_task_over_limit_suspends_with_incident():
    engine = ProcessEngine(EngineConfig(max_participants=2))
    flow = (
        FlowBuilder("lim")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignees=["'u1'", "'u2'", "'u3'"])
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("lim", start_user="u_s")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]

    from flowork.engine.kernel import ParticipantLimitError

    with pytest.raises(ParticipantLimitError):
        engine.task.complete(task_a.id, actor="u_a")
    # 技术故障语义：挂起 + 故障单（可见、可修、可 retry），绝不静默截断
    assert engine.runtime.get_instance(instance_id).state == "suspended"
    incidents = engine.runtime.list_incidents(instance_id=instance_id)
    assert incidents and incidents[0]["state"] == "open"
    assert "超过上限" in incidents[0]["message"]


def test_cc_recipients_over_limit_raises():
    engine = ProcessEngine(EngineConfig(max_participants=1))
    flow = (
        FlowBuilder("limcc")
        .start()
        .user_task("a", assignee="'u_a'")
        .cc_task("notify", recipients=["'u_x'", "'u_y'"])
        .end()
        .connect("start", "a")
        .connect("a", "notify")
        .connect("notify", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("limcc", start_user="u_s")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]

    from flowork.engine.kernel import ParticipantLimitError

    with pytest.raises(ParticipantLimitError):
        engine.task.complete(task_a.id, actor="u_a")


def test_add_countersigner_over_limit_rejects_whole_batch():
    """加签是调用方请求错误：拒绝整笔（TaskNotAvailableError），实例不挂起。"""
    engine = ProcessEngine(EngineConfig(max_participants=2))
    flow = (
        FlowBuilder("limadd")
        .start()
        .user_task("a", assignees=["'u1'", "'u2'"], multi_instance=MultiInstance(mode="parallel"))
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("limadd", start_user="u_s")
    task_u1 = engine.task.list_tasks(instance_id=instance_id, assignee="u1")[0]

    with pytest.raises(TaskNotAvailableError, match="上限"):
        engine.task.add_countersigner(task_u1.id, ["u_extra"], actor="u1")
    # 整笔回滚：未新增待办，原名单不变，实例照常
    ready = engine.task.list_tasks(instance_id=instance_id, state="ready")
    assert sorted(t.assignee for t in ready) == ["u1", "u2"]
    assert engine.runtime.get_instance(instance_id).state == "running"


def test_within_limit_participants_unchanged():
    """上限内正常路由（护栏不误伤）。"""
    engine = ProcessEngine(EngineConfig(max_participants=3))
    flow = (
        FlowBuilder("ok")
        .start()
        .user_task(
            "a", assignees=["'u1'", "'u2'", "'u3'"], multi_instance=MultiInstance(mode="parallel")
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("ok", start_user="u_s")
    ready = engine.task.list_tasks(instance_id=instance_id, state="ready")
    assert len(ready) == 3
    for task in ready:
        engine.task.complete(task.id, actor=task.assignee)
    assert engine.runtime.get_instance(instance_id).state == "completed"


# —— 回归：既有语义不被第一批破坏 ——


def test_withdraw_and_reject_still_work(engine: ProcessEngine):
    instance_id = _linear(engine, "reg")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task_a.id, actor="u_a")
    revived = engine.task.withdraw(task_a.id, actor="u_a")
    assert (revived.activity_code, revived.assignee) == ("a", "u_a")
    engine.task.complete(revived.id, actor="u_a")
    with pytest.raises(TaskNotAvailableError):
        engine.task.complete(task_a.id, actor="u_a")  # 旧任务不可重办
