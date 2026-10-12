"""行业对标第三批 A8：consult 咨询（前加签的「挂起—拍板」两轮形态）。

依据 spec v2.3 §5.1（delta-detail A8 行业修正版）：
- consult＝「先收集意见、原办理人最后拍板」——派生 task_type=consult 的**意见任务**
  （独立工作项：独立记录/意见/审计；行业修正：不抄 C7 的 assignee 轮转）；
- 意见不占票：task_type=consult 的完成不进 approve/disapprove 计数、不触发出口
  （「不占票的加签」走 consult、「占票的加签」走 add_countersigner，两路分流）；
- 主任务软挂起：咨询期间（存在同链未完 consult）主任务不可操作
  （TaskNotAvailableError「咨询中」），守卫不经新任务状态；
- 全部意见交回后主任务自动解禁，发 task_consult_finished 事件（宿主据此提醒）。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.errors import TaskNotAvailableError


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _linear(engine: ProcessEngine, code: str = "c1") -> str:
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


def _history(engine: ProcessEngine, instance_id: str) -> list[tuple[str, str, str]]:
    return [
        (h.activity_code, h.assignee, h.outcome) for h in engine.history.task_history(instance_id)
    ]


def test_consult_creates_opinion_tasks_and_blocks_main(engine: ProcessEngine):
    instance_id = _linear(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]

    opinions = engine.task.consult(task_a.id, users=["u_law", "u_boss"], actor="u_a")
    assert [t.task_type for t in opinions] == ["consult", "consult"]
    assert all(t.available_actions == ["complete_consult"] for t in opinions)

    # 主任务软挂起：complete/reject/加签/转办全被拦（不引入新任务状态）
    with pytest.raises(TaskNotAvailableError, match="咨询"):
        engine.task.complete(task_a.id, actor="u_a")
    with pytest.raises(TaskNotAvailableError, match="咨询"):
        engine.task.reject(task_a.id, actor="u_a")
    with pytest.raises(TaskNotAvailableError, match="咨询"):
        engine.task.add_countersigner(task_a.id, ["u_extra"], actor="u_a")
    with pytest.raises(TaskNotAvailableError, match="咨询"):
        engine.task.transfer(task_a.id, to_user="u_other", actor="u_a")
    # 流程未推进：a 上只有主任务 + 两条咨询意见任务
    kinds = sorted(t.task_type for t in engine.task.list_tasks(instance_id=instance_id))
    assert kinds == ["approval", "consult", "consult"]


def test_consult_opinions_do_not_vote(engine: ProcessEngine):
    """意见不占票：agree 意见不进 approve_count、reject 意见不触发驳回。"""
    instance_id = _linear(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    opinions = engine.task.consult(task_a.id, users=["u_law", "u_boss"], actor="u_a")

    engine.task.complete_consult(opinions[0].id, actor="u_law", opinion="agree")
    engine.task.complete_consult(opinions[1].id, actor="u_boss", opinion="reject")
    # 意见留痕（type=consult，outcome=意见），节点未推进
    assert ("a", "u_law", "agree") in _history(engine, instance_id)
    assert ("a", "u_boss", "reject") in _history(engine, instance_id)
    assert engine.runtime.get_instance(instance_id).state == "running"


def test_main_unblocks_after_all_opinions_and_event_fires(engine: ProcessEngine):
    instance_id = _linear(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    opinions = engine.task.consult(task_a.id, users=["u_law"], actor="u_a")

    engine.task.complete_consult(opinions[0].id, actor="u_law", opinion="agree")
    # 解禁：主任务可办、流程推进
    engine.task.complete(task_a.id, actor="u_a")
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["b"]
    events = [e.event_name for e in engine.history.events(instance_id)]
    assert "task_consult_finished" in events


def test_consult_chain_survives_transfer(engine: ProcessEngine):
    """转办保持咨询链（root 链不变）：换人后咨询仍挂在原任务链上、继续拦新办理人。"""
    instance_id = _linear(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    opinions = engine.task.consult(task_a.id, users=["u_law"], actor="u_a")
    # 咨询未完不可转办——先交回意见再转办，再对新办理人发起咨询
    engine.task.complete_consult(opinions[0].id, actor="u_law", opinion="agree")
    moved = engine.task.transfer(task_a.id, to_user="u_a2", actor="u_a")

    engine.task.consult(moved.id, users=["u_hr"], actor="u_a2")
    with pytest.raises(TaskNotAvailableError, match="咨询"):
        engine.task.complete(moved.id, actor="u_a2")


def test_consult_task_cannot_complete_via_complete(engine: ProcessEngine):
    """consult 任务不走 complete/complete_assist（动作表+类型双拦）。"""
    instance_id = _linear(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    opinions = engine.task.consult(task_a.id, users=["u_law"], actor="u_a")
    with pytest.raises(TaskNotAvailableError):
        engine.task.complete(opinions[0].id, actor="u_law")
    with pytest.raises(TaskNotAvailableError):
        engine.task.complete_assist(opinions[0].id, actor="u_law")
