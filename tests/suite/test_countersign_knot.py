"""会签×驳回×时限耦合走查：票数边界 / 否决票 / 全员否决 / 逐项改派 / auto 收口 / 加签重算。

时限按任务项绑定：三人的会签各有自己的 notify/reassign/auto 三档 Job——
改派后仍不办，auto_approve 兜底收口，正是三族语义耦合的完整故事。
"""

from __future__ import annotations

from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers, covers_node
from tests.suite.invariants import assert_no_dangling
from tests.suite.sequence_audit import event_sequence, variable_chain
from tests.suite.walkthrough import complete_of, reject_of
from tests.suite.world_flows_knot import KNOT_MEMBERS


def _start_knot(engine: ProcessEngine) -> str:
    return engine.runtime.start_process(
        "suite_countersign_knot", start_user="u_dev_wu", variables={"note": ""}
    )


def _to_knot(engine: ProcessEngine, iid: str, **kwargs: object) -> list:
    """过快审进会签，返回会签待办（三人三项）。"""
    complete_of(engine, iid, "u_lead_be", **kwargs)
    return engine.task.list_tasks(instance_id=iid)


def _raw_assignees(engine: ProcessEngine, iid: str) -> set[str]:
    return {t.assignee for t in engine.task.list_tasks(instance_id=iid)}


@covers("countersign.approve_ratio", "countersign.auto_pass_remaining")
@covers_node("suite_countersign_knot", "start", "quick", "knot", "knot_end")
def test_ratio_threshold_two_of_three(suite_engine: ProcessEngine) -> None:
    """60% 三人会签：一票不动，两票即达（ceil 1.8→2），余者 auto_pass。"""
    tasks = _to_knot(suite_engine, iid := _start_knot(suite_engine))
    assert {t.assignee for t in tasks} == {"u_chief_acc", "u_cash", "u_cfo"}

    complete_of(suite_engine, iid, "u_chief_acc")
    assert suite_engine.runtime.get_instance(iid).state == "running"  # 1/3 不足

    complete_of(suite_engine, iid, "u_cash")  # 2/3 达 60%
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    outcomes = {(h.assignee, h.outcome) for h in suite_engine.history.task_history(iid)}
    assert ("u_cfo", "auto_pass") in outcomes  # 未投票者记 auto_pass
    assert_no_dangling(suite_engine)


@covers("countersign.disapprove_all_default")
def test_single_reject_is_one_disapprove_vote(suite_engine: ProcessEngine) -> None:
    """会签中途驳回＝一张否决票：否决出口缺省需全员，单人驳回不退回、继续投票。"""
    _to_knot(suite_engine, iid := _start_knot(suite_engine))
    complete_of(suite_engine, iid, "u_chief_acc")  # approve=1
    reject_of(suite_engine, iid, "u_cash", comment="金额有疑问")  # disapprove=1，未达全员

    assert suite_engine.runtime.get_instance(iid).state == "running"  # 不退回
    complete_of(suite_engine, iid, "u_cfo")  # approve=2/3 → 同意出口先达
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    outcomes = {(h.assignee, h.outcome) for h in suite_engine.history.task_history(iid)}
    assert ("u_cash", "reject") in outcomes  # 否决票留痕
    assert_no_dangling(suite_engine)


@covers("reject.cause_token", "reject.explicit_target")
def test_unanimous_reject_exits_node_and_rewinds(suite_engine: ProcessEngine) -> None:
    """全员否决：触发否决出口 → 节点以 reject 退出 → 回 start 复活发起人。"""
    _to_knot(suite_engine, iid := _start_knot(suite_engine))
    for assignee in ("u_chief_acc", "u_cash", "u_cfo"):
        reject_of(suite_engine, iid, assignee, comment="不同意")

    assert suite_engine.runtime.get_instance(iid).state == "running"
    start_tasks = suite_engine.task.list_tasks(instance_id=iid, assignee="u_dev_wu")
    assert len(start_tasks) == 1  # 发起人重报任务（复活原办理人）

    # 重报 → 重走 → 两票收口
    suite_engine.task.complete(start_tasks[0].id, actor="u_dev_wu", variables={"note": "改后再报"})
    complete_of(suite_engine, iid, "u_lead_be")
    complete_of(suite_engine, iid, "u_chief_acc")
    complete_of(suite_engine, iid, "u_cash")

    # 变量血缘：启动写入（engine）+ 重报写入（task_output）两节
    chain = variable_chain(suite_engine, iid, "note")
    assert [step["source"] for step in chain] == ["engine", "task_output"]
    assert chain[0]["old"] is None and chain[0]["new"] == ""  # 新建键 birth
    assert chain[1]["acted_by"] == "u_dev_wu" and chain[1]["new"] == "改后再报"
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(suite_engine)


@covers("deadline.reassign")
def test_deadline_reassign_hits_every_knot_item(suite_engine: ProcessEngine) -> None:
    """改派逐项生效：会签三项各持 Job，到点全部移交 u_lead_be（含 notify 事件）。"""
    tasks = _to_knot(suite_engine, iid := _start_knot(suite_engine))
    assert len(tasks) == 3

    suite_engine.config.now.advance(5)
    suite_engine.jobs.tick()  # notify@5：只预警不移交（timeout_warned 事件）
    assert _raw_assignees(suite_engine, iid) == {"u_chief_acc", "u_cash", "u_cfo"}
    assert "timeout_warned" in event_sequence(suite_engine, iid)

    suite_engine.config.now.advance(5)
    suite_engine.jobs.tick()  # reassign@10：三项全移交
    assert _raw_assignees(suite_engine, iid) == {"u_lead_be"}
    history = suite_engine.history.task_history(iid)
    assert all(h.acted_by == "system:timeout" for h in history if h.outcome == "transfer") or any(
        h.acted_by == "system:timeout" for h in history
    )

    # 移交后仍不办 → auto_approve@15 兜底收口（改派+自动通过的耦合闭环）
    suite_engine.config.now.advance(5)
    suite_engine.jobs.tick()
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(suite_engine)


@covers("ops.add_countersigner", "countersign.approve_ratio")
def test_add_countersigner_recomputes_ratio(suite_engine: ProcessEngine) -> None:
    """中途加签：3→4 人后 60% 需 3 票（ceil 2.4→3），出口公式按新 item_count 重算。"""
    tasks = _to_knot(suite_engine, iid := _start_knot(suite_engine))
    by_assignee = {t.assignee: t.id for t in tasks}
    suite_engine.task.add_countersigner(by_assignee["u_cash"], users=["u_lead_fe"], actor="u_cash")

    complete_of(suite_engine, iid, "u_chief_acc")
    complete_of(suite_engine, iid, "u_cash")
    assert suite_engine.runtime.get_instance(iid).state == "running"  # 2/4 不足 60%

    complete_of(suite_engine, iid, "u_cfo")  # 3/4 达 60%
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    outcomes = {(h.assignee, h.outcome) for h in suite_engine.history.task_history(iid)}
    assert ("u_lead_fe", "auto_pass") in outcomes
    assert_no_dangling(suite_engine)


def test_knot_member_roster_is_stable() -> None:
    """会签名册契约：三人常量与表达式清单一致（防 KNOT_MEMBERS 漂移）。"""
    assert KNOT_MEMBERS == ["'u_chief_acc'", "'u_cash'", "'u_cfo'"]
