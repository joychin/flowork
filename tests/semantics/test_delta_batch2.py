"""对标产品 对标第二批语义测试：A2 撤回下游闸门 / A1 动作表与 available_actions / A5 端口收口。

依据 spec v2.3 第九轮（docs/product/kernel-v2/对标明细详设.md）：
- A2：撤回＝取消当前+新建上游；下游（正向可达、不含自身）存在已完结令牌或非取消
  已办 → 拒绝并提示改用驳回（错误带进展明细）；并行兄弟不拦（非下游）。
- A1：动作表按 (state × task_type × 认领态) 声明，TaskInfo.available_actions 服务端
  算完下发；入口守卫 assert_action_allowed（不可用 → TaskNotAvailableError）。
- A5：WorkCalendar 死端口删除（api 快照已更新，见 tests/test_api_surface.py）。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.errors import TaskNotAvailableError
from flowork.persistence.orm import TaskRow
from flowork.tasks.actions import allowed_actions


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _three_node(engine: ProcessEngine, code: str = "b2") -> str:
    flow = (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .user_task("c", assignee="'u_c'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "c")
        .connect("c", "end")
    )
    engine.repository.deploy(flow.build())
    return engine.runtime.start_process(code, start_user="u_starter")


def _complete_of(engine: ProcessEngine, instance_id: str, assignee: str):
    task = engine.task.list_tasks(instance_id=instance_id, assignee=assignee)[0]
    return engine.task.complete(task.id, actor=assignee)


# —— A2：撤回下游闸门 ——


def test_withdraw_blocked_when_downstream_handled(engine: ProcessEngine):
    """下游已办（b 完成、c 待办）→ 拒撤回，错误带进展明细，实例与待办不动。"""
    instance_id = _three_node(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    engine.task.complete(task_a.id, actor="u_a")
    _complete_of(engine, instance_id, "u_b")  # 下游 b 已办，c 待办

    with pytest.raises(TaskNotAvailableError) as excinfo:
        engine.task.withdraw(task_a.id, actor="u_a")
    message = str(excinfo.value)
    assert "驳回" in message and "u_b" in message  # 明确指路 + 进展明细
    assert engine.runtime.get_instance(instance_id).state == "running"
    # 待办面不动：c 仍在
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["c"]


def test_withdraw_allowed_when_downstream_all_pending(engine: ProcessEngine):
    """下游全未办（b 待办）→ 可撤回（既有语义保持：取消未完令牌+原节点重建）。"""
    instance_id = _three_node(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    engine.task.complete(task_a.id, actor="u_a")

    revived = engine.task.withdraw(task_a.id, actor="u_a")
    assert (revived.activity_code, revived.assignee) == ("a", "u_a")
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["a"]


def test_withdraw_parallel_sibling_done_not_blocked(engine: ProcessEngine):
    """并行兄弟已办不拦撤回——兄弟不在本节点的正向可达集合内。"""
    flow = (
        FlowBuilder("par")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("p", assignee="'u_p'")
        .user_task("join", assignee="'u_j'", advance_policy="all")
        .end()
        .connect("start", "a")
        .connect("start", "p")
        .connect("a", "join")
        .connect("p", "join")
        .connect("join", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("par", start_user="u_s")
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    engine.task.complete(task_a.id, actor="u_a")
    _complete_of(engine, instance_id, "u_p")  # 兄弟分支已办

    revived = engine.task.withdraw(task_a.id, actor="u_a")  # 下游=join（等待中，未完结）→ 放行
    assert (revived.activity_code, revived.assignee) == ("a", "u_a")


def test_withdraw_twice_still_possible_after_first_withdraw(engine: ProcessEngine):
    """首次撤回后再次办理再撤回：被撤回取消的下游令牌是 cancelled，不算进展。"""
    instance_id = _three_node(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    engine.task.complete(task_a.id, actor="u_a")
    revived = engine.task.withdraw(task_a.id, actor="u_a")
    engine.task.complete(revived.id, actor="u_a")
    revived2 = engine.task.withdraw(revived.id, actor="u_a")
    assert (revived2.activity_code, revived2.assignee) == ("a", "u_a")


# —— A1：动作表与 available_actions ——


def _task_row(task_id: str = "t", **kwargs) -> TaskRow:
    fields: dict[str, object] = {
        "id": task_id,
        "instance_id": "i",
        "token_id": "k",
        "activity_code": "n",
        "name": "",
        "assignee": "u_x",
        "task_type": "approval",
        "state": "ready",
    }
    fields.update(kwargs)
    return TaskRow(**fields)  # type: ignore[arg-type]


def test_action_table_by_state_type_claim():
    assert sorted(allowed_actions(_task_row())) == [
        "add_countersigner",
        "assist",
        "complete",
        "consult",  # v2.3 第三批 A8：咨询（意见不占票）
        "reject",
        "transfer",
        "unclaim",
    ]
    # 候选任务：先认领
    assert allowed_actions(_task_row(assignee="")) == ["claim"]
    # 协办任务：只交还意见
    assert allowed_actions(_task_row(task_type="assist")) == ["complete_assist"]
    # 终态：无动作（活表理论上只存 ready，防御）
    assert allowed_actions(_task_row(state="completed")) == []


def test_available_actions_on_task_info(engine: ProcessEngine):
    instance_id = _three_node(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    assert "complete" in task_a.available_actions and "reject" in task_a.available_actions
    engine.task.assist(task_a.id, ["u_helper"], actor="u_a")
    assist_info = engine.task.list_tasks(instance_id=instance_id, assignee="u_helper")[0]
    assert assist_info.available_actions == ["complete_assist"]


def test_action_table_enforcement_on_entries(engine: ProcessEngine):
    """表守卫接线在入口：协办任务不可转办/加签，主任务不可 complete_assist。"""
    instance_id = _three_node(engine)
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    assist = engine.task.assist(task_a.id, ["u_helper"], actor="u_a")[0]
    with pytest.raises(TaskNotAvailableError):
        engine.task.transfer(assist.id, to_user="u_other", actor="u_helper")
    with pytest.raises(TaskNotAvailableError):
        engine.task.complete_assist(task_a.id, actor="u_a")


def test_candidate_task_cannot_transfer(engine: ProcessEngine):
    """候选（未认领）任务不在分派类可用集——先 claim。"""
    flow = (
        FlowBuilder("cand")
        .start()
        .user_task("a", candidate_users=["'u_c1'"])
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("cand", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    assert task.available_actions == ["claim"]
    with pytest.raises(TaskNotAvailableError):
        engine.task.transfer(task.id, to_user="u_other", actor="u_c1")


# —— A5：WorkCalendar 端口收口（快照测试守卫，此处补语义断言） ——


def test_work_calendar_removed_from_public_surface():
    import flowork.api

    assert not hasattr(flowork.api, "WorkCalendar")
