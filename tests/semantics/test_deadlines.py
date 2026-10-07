"""P1-8（kernel-v2 spec §5.1/§7/§16）：超时演进测试。

deadline 列表（多级预警/改派/升级/自动通过·驳回）＋实例级 deadline（mark/cancel/
terminate）＋escalate 兜底；旧 timeout 字段兼容读取与 semantic diff 等价。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import dump_definition, load_definition
from flowork.definitions.node import TaskDeadline, TimeoutPolicy, effective_deadlines
from flowork.definitions.semantic_diff import semantic_diff
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.events.domain import DomainEvent
from flowork.participants.org_directory import OrgDirectory
from flowork.persistence.orm import EventOutboxRow, IncidentRow, TaskRow


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 1, 9, 0, 0)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, minutes: int) -> None:
        from datetime import timedelta

        self.value += timedelta(minutes=minutes)


def build_engine(org: OrgDirectory | None = None) -> tuple[ProcessEngine, FakeClock]:
    clock = FakeClock()
    config = EngineConfig(now=clock)
    if org is not None:
        config.org = org
    return ProcessEngine(config), clock


def deploy(engine: ProcessEngine, definition) -> None:
    engine.repository.deploy(definition)


def task_of(engine: ProcessEngine, instance_id: str) -> TaskRow:
    tasks = engine.task.list_tasks(instance_id=instance_id)
    assert tasks
    return tasks[0]


def events_named(engine: ProcessEngine, name: str) -> list[dict]:
    with engine.session_factory() as session:
        return [
            row.payload for row in session.scalars(select(EventOutboxRow)) if row.event_name == name
        ]


def test_multi_level_notify_fires_in_order():
    """验收：多级预警按序触发（T-3 天、T-1 天各提醒一次——两条 notify deadline）。"""
    engine, clock = build_engine()
    deploy(
        engine,
        FlowBuilder("ml_notify")
        .start()
        .user_task(
            "a",
            assignee="u_a",
            deadlines=[
                TaskDeadline(after_minutes=10, action="notify"),
                TaskDeadline(after_minutes=20, action="notify"),
            ],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(),
    )
    instance_id = engine.runtime.start_process("ml_notify", start_user="u_s")
    task = task_of(engine, instance_id)
    assert task.due_at is not None  # 首条到期时刻物化

    clock.advance(10)
    assert engine.jobs.tick() == 1  # 第一级预警
    clock.advance(10)
    assert engine.jobs.tick() == 1  # 第二级预警
    assert len(events_named(engine, "timeout_warned")) == 2


def test_auto_approve_stops_remaining_deadlines():
    """验收：auto_approve 终结等待后，剩余 deadline 不再触发。"""
    engine, clock = build_engine()
    deploy(
        engine,
        FlowBuilder("aa_stop")
        .start()
        .user_task(
            "a",
            assignee="u_a",
            deadlines=[
                TaskDeadline(after_minutes=30, action="auto_approve"),
                TaskDeadline(after_minutes=60, action="notify"),
            ],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(),
    )
    instance_id = engine.runtime.start_process("aa_stop", start_user="u_s")
    clock.advance(30)
    engine.jobs.tick()  # auto_approve → 实例完成
    assert engine.runtime.get_instance(instance_id).state == "completed"
    clock.advance(30)
    engine.jobs.tick()  # 剩余 notify Job 到期照跑，但动作被跳过（任务已终结）
    assert events_named(engine, "timeout_warned") == []  # 预警未触发


def test_reassign_deadline_transfers_task():
    engine, clock = build_engine()
    deploy(
        engine,
        FlowBuilder("reassign_flow")
        .start()
        .user_task(
            "a",
            assignee="u_a",
            deadlines=[TaskDeadline(after_minutes=15, action="reassign", reassign_to="u_backup")],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(),
    )
    instance_id = engine.runtime.start_process("reassign_flow", start_user="u_s")
    clock.advance(15)
    engine.jobs.tick()
    # 改派后新任务在 u_backup 名下，流程仍停在该节点
    tasks = engine.task.list_tasks(instance_id=instance_id)
    assert [(t.activity_code, t.assignee) for t in tasks] == [("a", "u_backup")]


def test_escalate_goes_to_manager_and_no_manager_raises_incident():
    """验收：escalate＝目录上级改派；查无上级 → 故障单（不静默丢）。"""
    org = OrgDirectory.from_dict(
        {"users": {"u_a": {"manager": "u_boss"}, "u_b": {}}, "departments": {}}
    )
    engine, clock = build_engine(org)
    deploy(
        engine,
        FlowBuilder("esc_flow")
        .start()
        .user_task(
            "a",
            assignee="u_a",
            deadlines=[TaskDeadline(after_minutes=15, action="escalate")],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(),
    )
    instance_id = engine.runtime.start_process("esc_flow", start_user="u_s")
    clock.advance(15)
    engine.jobs.tick()  # u_a 有上级 u_boss → 改派
    assert [t.assignee for t in engine.task.list_tasks(instance_id=instance_id)] == ["u_boss"]
    assert events_named(engine, DomainEvent.TASK_ESCALATED.value)

    # 无上级者（u_b 是顶层）触发 escalate → 故障单
    deploy(
        engine,
        FlowBuilder("esc_top")
        .start()
        .user_task(
            "b",
            assignee="u_b",
            deadlines=[TaskDeadline(after_minutes=15, action="escalate")],
        )
        .end()
        .connect("start", "b")
        .connect("b", "end")
        .build(),
    )
    top_id = engine.runtime.start_process("esc_top", start_user="u_s")
    clock.advance(15)
    engine.jobs.tick()
    with engine.session_factory() as session:
        assert (
            session.scalars(select(IncidentRow).where(IncidentRow.instance_id == top_id)).first()
            is not None
        )


def test_legacy_timeout_field_equivalent():
    """验收：旧 TimeoutPolicy 兼容读取生效，且与 deadlines 形态 semantic diff 等价。"""
    legacy = (
        FlowBuilder("legacy_t")
        .start()
        .user_task(
            "a", assignee="u_a", timeout=TimeoutPolicy(due_minutes=120, strategy="auto_approve")
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    assert [d.action for d in effective_deadlines(legacy.nodes[1])] == ["auto_approve"]
    assert effective_deadlines(legacy.nodes[1])[0].after_minutes == 120

    modern = (
        FlowBuilder("modern_t")
        .start()
        .user_task(
            "a",
            assignee="u_a",
            deadlines=[TaskDeadline(after_minutes=120, action="auto_approve")],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    modern.nodes[1].code = "a"
    registry = None
    from flowork.runtime.registry import create_registry

    registry = create_registry()
    legacy_def = load_definition(
        dump_definition(
            FlowBuilder("same")
            .start()
            .user_task(
                "a", assignee="u_a", timeout=TimeoutPolicy(due_minutes=120, strategy="auto_approve")
            )
            .end()
            .connect("start", "a")
            .connect("a", "end")
            .build()
        ),
        registry,
    )
    modern_def = load_definition(
        dump_definition(
            FlowBuilder("same")
            .start()
            .user_task(
                "a",
                assignee="u_a",
                deadlines=[TaskDeadline(after_minutes=120, action="auto_approve")],
            )
            .end()
            .connect("start", "a")
            .connect("a", "end")
            .build()
        ),
        registry,
    )
    assert semantic_diff(legacy_def, modern_def) == []  # 两形态等价零 diff


def test_instance_deadline_mark_cancel_terminate():
    """验收：实例到点三策略——mark 打标继续跑 / cancel 作废 / terminate 终止。"""
    for strategy, expected in (
        ("mark", "running"),
        ("cancel", "cancelled"),
        ("terminate", "terminated"),
    ):
        engine, clock = build_engine()
        definition = (
            FlowBuilder(f"ins_{strategy}")
            .start()
            .user_task("a", assignee="u_a")
            .end()
            .connect("start", "a")
            .connect("a", "end")
            .build()
        )
        definition.deadline = {"after_minutes": 60, "strategy": strategy}
        deploy(engine, definition)
        instance_id = engine.runtime.start_process(f"ins_{strategy}", start_user="u_s")
        assert engine.runtime.get_instance(instance_id).state == "running"
        clock.advance(60)
        engine.jobs.tick()
        assert engine.runtime.get_instance(instance_id).state == expected
        assert events_named(engine, DomainEvent.INSTANCE_DEADLINE_REACHED.value)
        if strategy == "mark":
            with engine.session_factory() as session:
                from flowork.persistence.orm import ProcessInstanceRow

                row = session.get(ProcessInstanceRow, instance_id)
                assert row is not None and row.deadline_marked is True


def test_instance_deadline_silent_after_completion():
    """实例已完成则静默不触发（spec §7）。"""
    engine, clock = build_engine()
    definition = (
        FlowBuilder("ins_done")
        .start()
        .user_task("a", assignee="u_a")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    definition.deadline = {"after_minutes": 60, "strategy": "cancel"}
    deploy(engine, definition)
    instance_id = engine.runtime.start_process("ins_done", start_user="u_s")
    task = task_of(engine, instance_id)
    engine.task.complete(task.id, actor="u_a")  # 先完成
    clock.advance(60)
    engine.jobs.tick()
    assert engine.runtime.get_instance(instance_id).state == "completed"  # 不被作废


def test_instance_deadline_cancel_emits_terminal_event():
    """REVIEW P1-⑤回归：时限作废/终止与 runtime 路径同款终态事件。"""
    engine, clock = build_engine()
    definition = (
        FlowBuilder("ins_evt")
        .start()
        .user_task("a", assignee="u_a")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    definition.deadline = {"after_minutes": 30, "strategy": "cancel"}
    deploy(engine, definition)
    instance_id = engine.runtime.start_process("ins_evt", start_user="u_s")
    clock.advance(30)
    engine.jobs.tick()
    assert engine.runtime.get_instance(instance_id).state == "cancelled"
    payloads = events_named(engine, DomainEvent.PROCESS_CANCELLED.value)
    assert payloads and payloads[0]["reason"] == "instance_deadline"
