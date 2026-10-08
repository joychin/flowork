"""能力域③流程运行状态查看：实例状态机、待办/已办查询、历史只追加、
事件流、令牌、轨迹、只读预判、传阅件、归档导出。"""

from __future__ import annotations

import pytest

from flowork.engine.engine import ProcessEngine
from flowork.errors import TaskNotAvailableError
from flowork.events.domain import DomainEvent
from tests.suite.features import covers
from tests.suite.walkthrough import complete_of, reject_of


def start_expense(engine: ProcessEngine, amount: float = 8000) -> str:
    return engine.runtime.start_process(
        "suite_expense",
        start_user="u_dev_wu",
        variables={"amount": amount, "expense_type": "travel"},
    )


def drive_high_lane_complete(engine: ProcessEngine) -> str:
    """高额道全办结，产出多节点历史供查询面断言。"""
    iid = start_expense(engine)
    for assignee in ("u_chief_acc", "u_cash", "u_cfo", "u_ceo"):
        complete_of(engine, iid, assignee)
    return iid


@covers("query.instance_states", "lifecycle.pause_resume")
def test_instance_state_machine_visible(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = start_expense(engine, amount=1200)
    assert engine.runtime.get_instance(iid).state == "running"

    engine.runtime.pause_process(iid, actor="u_cto", reason="对账冻结")
    assert engine.runtime.get_instance(iid).state == "paused"
    with pytest.raises(TaskNotAvailableError):
        complete_of(engine, iid, "u_lead_be")

    engine.runtime.resume_process(iid, actor="u_cto")
    assert engine.runtime.get_instance(iid).state == "running"
    complete_of(engine, iid, "u_lead_be")
    assert engine.runtime.get_instance(iid).state == "completed"


@covers("lifecycle.terminate", "lifecycle.cancel", "query.instance_states")
def test_terminated_and_cancelled_states_visible(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    terminated = start_expense(engine, amount=1200)
    engine.runtime.terminate_process(terminated, actor="u_ceo", reason="违规")
    assert engine.runtime.get_instance(terminated).state == "terminated"

    cancelled = start_expense(engine, amount=1200)
    engine.runtime.cancel_process(cancelled, actor="u_dev_wu", reason="撤单")
    assert engine.runtime.get_instance(cancelled).state == "cancelled"
    assert engine.history.instances(state="cancelled")[0].id == cancelled


@covers("query.list_tasks")
def test_list_tasks_by_instance_assignee_and_state(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    first = start_expense(engine)
    second = start_expense(engine)

    wu_tasks = engine.task.list_tasks(assignee="u_chief_acc")
    assert {t.instance_id for t in wu_tasks} == {first, second}

    complete_of(engine, first, "u_chief_acc")
    assert {t.id for t in engine.task.list_tasks(instance_id=first)} == {
        t.id for t in engine.task.list_tasks(instance_id=first, state="ready")
    }
    assert engine.task.list_tasks(instance_id=first, assignee="u_chief_acc") == []


@covers("query.paged_tasks")
def test_paged_tasks_pagination(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = drive_high_lane_complete(engine)
    page1 = engine.history.paged_tasks(instance_id=iid, offset=0, limit=2)
    assert page1.total == 4 and len(page1.items) == 2
    page2 = engine.history.paged_tasks(instance_id=iid, offset=2, limit=2)
    assert len(page2.items) == 2
    ids1 = {item.task_id for item in page1.items}
    ids2 = {item.task_id for item in page2.items}
    assert not ids1 & ids2, "分页不得重叠"
    assert ids1 | ids2 == {row.task_id for row in engine.history.task_history(instance_id=iid)}


@covers("query.task_history_append_only", "reject.cause_token")
def test_task_history_append_only_with_revival_trace(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = start_expense(engine)
    reject_of(engine, iid, "u_chief_acc")
    snapshot_before = [
        (row.id, row.outcome) for row in engine.history.task_history(instance_id=iid)
    ]
    complete_of(engine, iid, "u_dev_wu", variables={"amount": 900})
    complete_of(engine, iid, "u_lead_be")

    snapshot_after = [(row.id, row.outcome) for row in engine.history.task_history(instance_id=iid)]
    assert snapshot_before == snapshot_after[: len(snapshot_before)], "已办只追加不修改"
    assert any(
        row.cause_token_id
        for row in engine.history.task_history(instance_id=iid, assignee="u_cash")
    ), "驳回连带归档的待办带 cause_token_id"


@covers("query.events_stream")
def test_events_stream_replays_full_lifecycle(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = drive_high_lane_complete(engine)
    names = [event.event_name for event in engine.history.events(iid)]
    assert names[0] == DomainEvent.PROCESS_STARTED
    assert DomainEvent.PROCESS_COMPLETED in names
    assert names.count(DomainEvent.TASK_COMPLETED) == 4
    assert "payment_executed" in names, "service 动作事件入流"
    assert engine.history.events(instance_id=None, limit=1000), "全局事件流可查"


@covers("query.tokens")
def test_tokens_expose_counters(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = start_expense(engine)
    complete_of(engine, iid, "u_chief_acc")
    token = next(t for t in engine.history.tokens(iid) if t.activity_code == "fin_countersign")
    assert (token.item_count, token.approve_count) == (2, 1)
    assert token.state == "unfinished"


@covers("query.trace_instance")
def test_trace_instance_merges_timeline(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = drive_high_lane_complete(engine)
    trace = engine.history.trace_instance(iid)
    kinds = {entry.kind for entry in trace}
    assert kinds == {"token", "task", "event"}
    times = [entry.time for entry in trace]
    assert times == sorted(times), "轨迹按时间合并有序"


@covers("query.anticipate")
def test_anticipate_is_readonly(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = start_expense(engine, amount=1200)
    task = engine.task.list_tasks(instance_id=iid, assignee="u_lead_be")[0]
    before = engine.runtime.get_instance(iid).state

    verdict = engine.task.anticipate(task.id, "agree")
    assert verdict.node_exit and verdict.instance_complete

    assert engine.runtime.get_instance(iid).state == before
    assert engine.task.list_tasks(instance_id=iid, assignee="u_lead_be"), "预判不消费待办"


@covers("query.cc_list", "ops.cc_read")
def test_cc_items_listed_and_marked_read(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = start_expense(engine, amount=1200)
    complete_of(engine, iid, "u_lead_be")

    items = engine.task.list_cc_tasks(recipient="u_cfo")
    assert len(items) == 1 and items[0].instance_id == iid and items[0].activity_code == "cc_fin"
    assert not items[0].is_read
    engine.task.mark_cc_read(items[0].id, "u_cfo")
    assert engine.task.list_cc_tasks(recipient="u_cfo")[0].is_read


@covers("query.archive_instance")
def test_archive_instance_exports_full_bundle(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = drive_high_lane_complete(engine)
    archive = engine.history.archive_instance(iid)
    assert set(archive) == {"instance", "tokens", "task_history", "cc_items", "events"}
    assert archive["instance"]["state"] == "completed"
    assert archive["instance"]["variables"]["amount"] == 8000
    assert len(archive["task_history"]) == 4
