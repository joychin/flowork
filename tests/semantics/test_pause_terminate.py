"""P1-3（kernel-v2 spec §7/§16）：pause/resume/terminate + 暂停期 timer 冻结测试。

验收：暂停期间定时器不触发（tick 冻结），恢复后按暂停时长顺延；终止为终态
terminated（与业务取消 cancelled 区分）。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.node import TimeoutPolicy
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import EventOutboxRow


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 1, 9, 0, 0)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, minutes: int) -> None:
        hour, minute = divmod(minutes, 60)
        from datetime import timedelta

        self.value = self.value + timedelta(minutes=minute, hours=hour)


def build_engine() -> tuple[ProcessEngine, FakeClock]:
    clock = FakeClock()
    engine = ProcessEngine(EngineConfig(now=clock))
    definition = (
        FlowBuilder("pause_flow")
        .start()
        .user_task(
            "a",
            assignee="'u_a'",
            timeout=TimeoutPolicy(due_minutes=120, strategy="auto_approve"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(definition)
    return engine, clock


def events_of(engine: ProcessEngine, name: str) -> list[dict]:
    with engine.session_factory() as session:
        return [
            row.payload for row in session.scalars(select(EventOutboxRow)) if row.event_name == name
        ]


def test_pause_blocks_task_completion():
    engine, _clock = build_engine()
    instance_id = engine.runtime.start_process("pause_flow", start_user="u_s")
    engine.runtime.pause_process(instance_id, actor="admin")
    assert engine.runtime.get_instance(instance_id).state == "paused"

    task = engine.task.list_tasks(instance_id=instance_id)[0]
    with pytest.raises(FloworkError, match="暂停"):
        engine.task.complete(task.id, actor="u_a")
    engine.runtime.resume_process(instance_id, actor="admin")
    engine.task.complete(task.id, actor="u_a")  # 恢复后可办
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_timer_frozen_while_paused_and_deferred_after_resume():
    """验收（P1-3）：暂停期 timer 冻结；恢复后 run_at 顺延暂停时长。"""
    engine, clock = build_engine()
    instance_id = engine.runtime.start_process("pause_flow", start_user="u_s")

    clock.advance(30)  # T+30m：暂停（超时应在 T+120m 触发）
    engine.runtime.pause_process(instance_id, actor="admin")
    clock.advance(200)  # T+230m：原定 T+120m 已过，但暂停冻结
    assert engine.jobs.tick() == 0
    assert engine.runtime.get_instance(instance_id).state == "paused"

    clock.advance(10)  # T+240m：恢复——timer 应顺延至 T+120m+210m=T+330m
    engine.runtime.resume_process(instance_id, actor="admin")
    assert engine.jobs.tick() == 0  # 尚未到顺延后的触发时刻
    clock.advance(90)  # T+330m：到点触发（auto_approve）
    assert engine.jobs.tick() == 1
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_pause_resume_events_recorded():
    engine, _clock = build_engine()
    instance_id = engine.runtime.start_process("pause_flow", start_user="u_s")
    engine.runtime.pause_process(instance_id, actor="boss", reason="等外部材料")
    engine.runtime.resume_process(instance_id, actor="boss")
    paused = events_of(engine, DomainEvent.PROCESS_PAUSED.value)
    resumed = events_of(engine, DomainEvent.PROCESS_RESUMED.value)
    assert paused and paused[0]["reason"] == "等外部材料"
    assert resumed and "deferred_minutes" in resumed[0]


def test_terminate_is_terminal_and_distinct_from_cancel():
    engine, _clock = build_engine()
    instance_id = engine.runtime.start_process("pause_flow", start_user="u_s")
    engine.runtime.pause_process(instance_id, actor="admin")
    engine.runtime.terminate_process(instance_id, actor="admin", reason="合规下线")  # 暂停中可终止

    info = engine.runtime.get_instance(instance_id)
    assert info.state == "terminated"
    assert events_of(engine, DomainEvent.PROCESS_TERMINATED.value)
    assert engine.task.list_tasks(instance_id=instance_id) == []  # 待办已撤

    # 终态不可再变
    with pytest.raises(FloworkError):
        engine.runtime.terminate_process(instance_id, actor="admin")
    with pytest.raises(FloworkError):
        engine.runtime.resume_process(instance_id, actor="admin")


def test_double_pause_rejected():
    engine, _clock = build_engine()
    instance_id = engine.runtime.start_process("pause_flow", start_user="u_s")
    engine.runtime.pause_process(instance_id, actor="a")
    with pytest.raises(FloworkError):
        engine.runtime.pause_process(instance_id, actor="a")


def test_paused_blocks_all_task_actions():
    """REVIEW P1-③回归：暂停期 reject/claim/transfer 全部拦截，恢复后放行。"""
    from flowork.errors import FloworkError

    engine, _clock = build_engine()
    instance_id = engine.runtime.start_process("pause_flow", start_user="u_s")
    engine.runtime.pause_process(instance_id, actor="admin")
    task = engine.task.list_tasks(instance_id=instance_id)[0]

    with pytest.raises(FloworkError, match="暂停"):
        engine.task.reject(task.id, actor="u_a")
    with pytest.raises(FloworkError, match="暂停"):
        engine.task.claim(task.id, "u_a")
    with pytest.raises(FloworkError, match="暂停"):
        engine.task.transfer(task.id, to_user="u_b", actor="u_a")
    assert engine.task.list_tasks(instance_id=instance_id, assignee="u_a")  # 待办原样

    engine.runtime.resume_process(instance_id, actor="admin")
    engine.task.complete(task.id, actor="u_a")  # 恢复后放行
    assert engine.runtime.get_instance(instance_id).state == "completed"
