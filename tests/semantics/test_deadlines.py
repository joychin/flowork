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
            assignee="'u_a'",
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
            assignee="'u_a'",
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
            assignee="'u_a'",
            deadlines=[TaskDeadline(after_minutes=15, action="reassign", reassign_to="'u_backup'")],
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


def test_escalate_goes_to_expression_target_and_failure_raises_incident():
    """验收：escalate 目标由 `escalate_to` 表达式给出（v2.3 第十四轮）。

    引擎不含「上级」概念（spec §5.5 分工铁律）——科层组织在图纸里写
    `manager_of(task.assignee)`；求值出空 → 故障单（不静默丢）。
    """
    from flowork import contrib

    org = OrgDirectory.from_dict(
        {"users": {"u_a": {"manager": "u_boss"}, "u_b": {}}, "departments": {}}
    )
    clock = FakeClock()
    engine = ProcessEngine(
        EngineConfig(now=clock, org=org, expression_functions=contrib.cel_vocabulary(org))
    )
    deploy(
        engine,
        FlowBuilder("esc_flow")
        .start()
        .user_task(
            "a",
            assignee="'u_a'",
            deadlines=[
                TaskDeadline(
                    after_minutes=15,
                    action="escalate",
                    escalate_to="manager_of(task.assignee)",
                )
            ],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(),
    )
    instance_id = engine.runtime.start_process("esc_flow", start_user="u_s")
    clock.advance(15)
    engine.jobs.tick()  # u_a 有上级 u_boss → 升级改派
    assert [t.assignee for t in engine.task.list_tasks(instance_id=instance_id)] == ["u_boss"]
    assert events_named(engine, DomainEvent.TASK_ESCALATED.value)

    # 无上级者（u_b 是顶层）：表达式求值出 None → 故障单
    deploy(
        engine,
        FlowBuilder("esc_top")
        .start()
        .user_task(
            "b",
            assignee="'u_b'",
            deadlines=[
                TaskDeadline(
                    after_minutes=15,
                    action="escalate",
                    escalate_to="manager_of(task.assignee)",
                )
            ],
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


def test_escalate_works_without_directory_engine():
    """无目录引擎也能升级：目标纯来自图纸，引擎零组织假设（spec §5.5）。"""
    engine, clock = build_engine()  # 无 org
    deploy(
        engine,
        FlowBuilder("esc_nodir")
        .start()
        .user_task(
            "a",
            assignee="'u_a'",
            deadlines=[TaskDeadline(after_minutes=15, action="escalate", escalate_to="'u_lead'")],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(),
    )
    instance_id = engine.runtime.start_process("esc_nodir", start_user="u_s")
    clock.advance(15)
    engine.jobs.tick()
    assert [t.assignee for t in engine.task.list_tasks(instance_id=instance_id)] == ["u_lead"]
    assert events_named(engine, DomainEvent.TASK_ESCALATED.value)


def test_legacy_timeout_field_equivalent():
    """验收：旧 TimeoutPolicy 兼容读取生效，且与 deadlines 形态 semantic diff 等价。"""
    legacy = (
        FlowBuilder("legacy_t")
        .start()
        .user_task(
            "a", assignee="'u_a'", timeout=TimeoutPolicy(due_minutes=120, strategy="auto_approve")
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
            assignee="'u_a'",
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
                "a",
                assignee="'u_a'",
                timeout=TimeoutPolicy(due_minutes=120, strategy="auto_approve"),
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
                assignee="'u_a'",
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
            .user_task("a", assignee="'u_a'")
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
        .user_task("a", assignee="'u_a'")
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
        .user_task("a", assignee="'u_a'")
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
    # kernel.complete_instance 亦发一条（无 reason）；deadline 路径带 reason=instance_deadline
    assert any(p.get("reason") == "instance_deadline" for p in payloads)


def test_reassign_expression_with_task_context():
    """改派表达式 fire 时求值（spec §4.2/C.4）：manager_of(task.assignee) 用当前办理人。"""
    from flowork import contrib
    from flowork.engine.config import EngineConfig
    from flowork.participants.org_directory import OrgDirectory

    org = OrgDirectory.from_dict({"users": {"u_clerk": {"manager": "u_boss"}, "u_boss": {}}})
    clock = FakeClock()
    engine = ProcessEngine(
        EngineConfig(
            now=clock,
            org=org,
            expression_functions=contrib.cel_vocabulary(org),
        )
    )
    flow = (
        FlowBuilder("reassign_expr")
        .start()
        .user_task(
            "a",
            assignee="'u_clerk'",
            deadlines=[
                TaskDeadline(
                    after_minutes=15, action="reassign", reassign_to="manager_of(task.assignee)"
                )
            ],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("reassign_expr", start_user="u_x")
    clock.advance(15)
    engine.jobs.tick()
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    assert task.assignee == "u_boss"  # 办理人上级接手


def test_reassign_expression_error_becomes_incident():
    """改派公式求值错 → Job 失败路径（死信+故障单，对齐 escalate 语义）。"""
    clock = FakeClock()
    engine = ProcessEngine(EngineConfig(now=clock))
    flow = (
        FlowBuilder("reassign_bad")
        .start()
        .user_task(
            "a",
            assignee="'u_a'",
            deadlines=[TaskDeadline(after_minutes=15, action="reassign", reassign_to="nosuchvar")],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    engine.repository.deploy(flow.build())
    engine.runtime.start_process("reassign_bad", start_user="u_x")
    clock.advance(15)
    engine.jobs.tick()
    from sqlalchemy import select as _select

    from flowork.persistence.orm import JobRow as _JobRow

    session = engine.session_factory()
    job = session.scalars(_select(_JobRow)).first()
    session.close()
    assert job is not None and "超时reassign公式求值失败" in (job.last_error or "")
    assert engine.runtime.list_incidents(), "改派公式错必须落故障单（首败即记）"
