"""时间线长程走查（测试套件完善批 5/5）：时间交错下的流转全程。

现有时间语义测试都是单点（暂停冻结/改派/竞速各自为战）；这里用 FakeClock
把一条 knot 实例从头推到尾：预警→人工转办→到时改派→暂停冻结（零事件窗）→
恢复→补票收口，按事件全序（(occurred_at, id)）钉死时序契约。另附 wait
message+timeout 竞速两形态路由（elapsed 先到/消息先到）。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition
from flowork.engine.engine import ProcessEngine
from tests.suite.invariants import assert_full_ledger
from tests.suite.sequence_audit import route_rows
from tests.suite.walkthrough import complete_of


def _event_names(engine: ProcessEngine, iid: str) -> list[str]:
    return [event.event_name for event in engine.history.events(iid, limit=1000)]


def _only_index(names: list[str], name: str) -> int:
    hits = [i for i, n in enumerate(names) if n == name]
    assert len(hits) == 1, f"{name} 应恰一次，实得 {len(hits)} 次"
    return hits[0]


def test_knot_timeline_journey(suite_engine: ProcessEngine) -> None:
    """knot 长剧本：预警→转办→到时改派→暂停冻结→恢复→补票，全程时序钉死。"""
    engine = suite_engine
    iid = engine.runtime.start_process(
        "suite_countersign_knot", start_user="u_dev_wu", variables={"note": "时间线"}
    )
    complete_of(engine, iid, "u_lead_be")  # 快审 → 会签三项
    complete_of(engine, iid, "u_chief_acc")  # 投 1 票（1/3，60% 出口未达）

    # t+5：notify 到期 → 预警
    engine.config.now.advance(5)  # type: ignore[attr-defined]
    engine.jobs.tick()
    assert "timeout_warned" in _event_names(engine, iid)

    # 人工转办：u_cash 的会签项移交 u_buyer（时限 Job 随任务走，run_at 不变）
    cash = engine.task.list_tasks(instance_id=iid, assignee="u_cash")[0]
    engine.task.transfer(cash.id, to_user="u_buyer", actor="u_cash")

    # t+10：reassign 到期 → 余下未办项移交 u_lead_be
    engine.config.now.advance(5)  # type: ignore[attr-defined]
    engine.jobs.tick()
    assert engine.task.list_tasks(instance_id=iid, assignee="u_lead_be"), "改派应移交 u_lead_be"

    # 暂停 → t+40：冻结窗口内 tick 零事件（auto_approve@15 不得偷跑）
    engine.runtime.pause_process(iid, actor="ops")
    frozen_start = len(_event_names(engine, iid))
    engine.config.now.advance(30)  # type: ignore[attr-defined]
    engine.jobs.tick()
    frozen_events = _event_names(engine, iid)[frozen_start:]
    assert frozen_events == [], f"暂停冻结窗内不得有事件：{frozen_events}"

    # 恢复 → 补一票（2/3 ≥ 60%）→ 出口达成，余项 auto_pass 收口
    engine.runtime.resume_process(iid, actor="ops")
    lead = engine.task.list_tasks(instance_id=iid, assignee="u_lead_be")[0]
    engine.task.complete(lead.id, actor="u_lead_be")
    assert engine.runtime.get_instance(iid).state == "completed"

    # —— 时序契约（事件全序）：预警 < 人工转办 < 到时改派 < 暂停 < 恢复 < 完结 ——
    names = _event_names(engine, iid)
    events = engine.history.events(iid, limit=1000)
    manual_transfer = [
        i
        for i, e in enumerate(events)
        if e.event_name == "task_transferred" and e.payload.get("actor") == "u_cash"
    ]
    system_transfer = [
        i
        for i, e in enumerate(events)
        if e.event_name == "task_transferred"
        and str(e.payload.get("actor", "")).startswith("system")
    ]
    assert manual_transfer and system_transfer, names
    warn_last = max(i for i, n in enumerate(names[: manual_transfer[0]]) if n == "timeout_warned")
    assert warn_last < manual_transfer[0] < system_transfer[0], "预警先于人工转办先于到时改派"
    assert _only_index(names, "process_paused") < _only_index(names, "process_resumed")
    assert _only_index(names, "process_resumed") < _only_index(names, "process_completed")
    assert_full_ledger(engine)


def _race_world() -> ProcessDefinition:
    """wait 竞速世界：消息与到时双报，出边按 wait.result 择道。"""
    return (
        FlowBuilder("race_wait", "竞速等待")
        .start()
        .wait("hold", "等消息或到时", message={"name": "m1"}, timeout={"after_minutes": 5})
        .user_task("by_msg", "消息道", assignee="'u_a'")
        .user_task("by_time", "到时道", assignee="'u_b'")
        .end("race_end", "竞速结束")
        .flow("start", "hold")
        .flow("hold", "by_msg", condition='wait.result == "matched"')
        .flow("hold", "by_time", condition='wait.result == "elapsed"', default=True)
        .flow("by_msg", "race_end")
        .flow("by_time", "race_end")
        .build()
    )


@pytest.mark.parametrize("winner", ["elapsed", "matched"])
def test_wait_race_two_shapes(suite_engine: ProcessEngine, winner: str) -> None:
    """竞速两形态：到时先到走 elapsed 道 / 消息先到走 matched 道，路由行如实。"""
    engine = suite_engine
    engine.repository.deploy(_race_world())
    iid = engine.runtime.start_process("race_wait", start_user="u_s")
    if winner == "matched":
        assert engine.messages.publish_message("m1", payload={"doc": "D1"})
    engine.config.now.advance(6)  # type: ignore[attr-defined]
    engine.jobs.tick()
    # matched 胜 → 到时道不开待办；elapsed 胜 → 消息道不开待办
    chooser, winner_user = ("by_msg", "u_a") if winner == "matched" else ("by_time", "u_b")
    loser = "u_b" if winner == "matched" else "u_a"
    assert engine.task.list_tasks(instance_id=iid, assignee=loser) == [], "败者道不得开待办"
    complete_of(engine, iid, winner_user)
    assert engine.runtime.get_instance(iid).state == "completed"
    if winner == "elapsed":
        assert "timer_fired" in _event_names(engine, iid), "到时兑现应留 timer_fired"
    hold_rows = [r for r in route_rows(engine, iid) if r["source"] == "hold"]
    assert hold_rows and hold_rows[0]["selected"] == [f"hold->{chooser}"], hold_rows
    assert_full_ledger(engine)
