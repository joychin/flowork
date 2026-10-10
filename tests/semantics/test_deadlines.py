"""P1-8（kernel-v2 spec §5.1/§7/§16）：超时演进测试。

deadline 列表（多级预警/改派/升级/自动通过·驳回）＋实例级 deadline（mark/cancel/
terminate）＋escalate 兜底；旧 timeout 字段兼容读取与 semantic diff 等价。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable, dump_definition, load_definition
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
    engine.repository.deploy(
        flow.build().model_copy(
            update={"variables": [ProcessVariable(name="nosuchvar", type="number")]}
        )
    )
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


def test_suspended_instance_deadline_frozen_not_swallowed():
    """回归（code-review 2026-10 P0-5）：挂起实例的时限 Job 不被 tick 认领。

    并行臂故障挂起（b 臂办结触发 service_task 炸 → Incident + suspended，
    命令回滚）时，另一臂任务的 auto_approve 冻结；故障解决（实例恢复运行）
    后到点仍触发。修复前：挂起期照常认领，complete 被实例态拦下后被误标
    「task already finished」结案——时限永久消失且文案误导排障。
    """
    import pytest

    from flowork.definitions.builtin import RouteMode
    from flowork.errors import FloworkError
    from flowork.persistence.orm import JobRow

    engine, clock = build_engine()
    deploy(
        engine,
        FlowBuilder("susp_deadline")
        .start()
        .branch("route", route_mode=RouteMode.ALL_MATCH)
        .user_task(
            "a",
            assignee="'u_a'",
            deadlines=[TaskDeadline(after_minutes=30, action="auto_approve")],
        )
        .user_task("b", assignee="'u_b'")
        .service_task("boom", "会炸的步骤", action="explode", params={})
        .end(advance_policy="all")
        .connect("start", "route")
        .connect("route", "a")
        .connect("route", "b")
        .connect("b", "boom")
        .connect("a", "end")
        .connect("boom", "end")
        .build(),
    )

    def explode(ctx, params):
        raise RuntimeError("外部接口炸了")

    engine.register_handler("explode", explode)

    instance_id = engine.runtime.start_process("susp_deadline", start_user="u_s")
    tasks = {t.activity_code: t for t in engine.task.list_tasks(instance_id=instance_id)}
    assert set(tasks) == {"a", "b"}

    with pytest.raises(FloworkError):
        engine.task.complete(tasks["b"].id, actor="u_b")  # 触发 boom → 挂起
    assert engine.runtime.get_instance(instance_id).state == "suspended"
    task_a = engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0]
    assert task_a.state == "ready"

    clock.advance(30)
    assert engine.jobs.tick() == 0, "挂起期 timer 冻结：不认领、不消费"
    assert engine.task.list_tasks(instance_id=instance_id, assignee="u_a"), "任务未被自动通过"
    with engine.session_factory() as session:
        job = session.scalars(select(JobRow).where(JobRow.instance_id == instance_id)).first()
        assert job is not None and job.state == "scheduled", "时限 Job 未被 skip 结案"

    incidents = engine.runtime.list_incidents(instance_id=instance_id)
    # resume 只恢复运行态（skip 是管理员手术会视未完令牌为完成，不合本场景）
    engine.runtime.resolve_incident(incidents[0]["id"], action="resume", actor="ops")
    assert engine.runtime.get_instance(instance_id).state == "running"

    assert engine.jobs.tick() == 1, "恢复后到点仍触发（冻结是推迟不是吞掉）"
    history = engine.history.task_history(instance_id)
    assert any(
        h.activity_code == "a" and h.outcome == "agree" and h.acted_by == "system:timeout"
        for h in history
    ), [(h.activity_code, h.outcome, h.acted_by) for h in history]


def test_unclaimed_candidate_auto_approve_fires_via_system_claim():
    """回归（code-review 2026-10 P1-4）：候选任务未认领时超时策略代认领执行。

    图纸配置的节点级 SLA 不因候选池积压而空洞——到期无人认领时
    system:timeout 代认领后走统一执行路径。修复前四策略对未认领任务
    全部被静默 skip 消费（一次性 Job 结案，SLA 永久失效）。
    """
    from flowork.persistence.orm import JobRow

    engine, clock = build_engine()
    deploy(
        engine,
        FlowBuilder("honest_skip")
        .start()
        .user_task(
            "a",
            candidate_users=["'u_a'"],
            deadlines=[TaskDeadline(after_minutes=10, action="auto_approve")],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(),
    )
    instance_id = engine.runtime.start_process("honest_skip", start_user="u_s")
    assert engine.task.list_tasks(instance_id=instance_id)[0].assignee == ""  # 未认领
    clock.advance(10)
    engine.jobs.tick()
    history = engine.history.task_history(instance_id)
    assert any(
        h.activity_code == "a" and h.outcome == "agree" and h.acted_by == "system:timeout"
        for h in history
    ), [(h.activity_code, h.outcome, h.acted_by) for h in history]
    assert engine.runtime.get_instance(instance_id).state == "completed"
    with engine.session_factory() as session:
        job = session.scalars(select(JobRow).where(JobRow.instance_id == instance_id)).first()
        assert job is not None and job.state == "done"


# —— deadline 生命周期矩阵（code-review 2026-10 第二批：任务行生命周期 × deadline Job）——


def _serial_mi_engine():
    """串行会签三人 u_p1/u_p2/u_p3 + auto_approve@30（节点级 SLA）。"""
    from flowork.definitions.node import MultiInstance

    engine, clock = build_engine()
    deploy(
        engine,
        FlowBuilder("serial_mi")
        .start()
        .user_task(
            "c",
            assignees=["'u_p1'", "'u_p2'", "'u_p3'"],
            multi_instance=MultiInstance(mode="sequential"),
            deadlines=[TaskDeadline(after_minutes=30, action="auto_approve")],
        )
        .end()
        .connect("start", "c")
        .connect("c", "end")
        .build(),
    )
    return engine, clock


def _scheduled_jobs(engine, instance_id):
    from flowork.persistence.orm import JobRow

    with engine.session_factory() as session:
        return [
            (j.payload.get("task_id"), j.state)
            for j in session.scalars(
                select(JobRow).where(
                    JobRow.instance_id == instance_id, JobRow.job_type == "timer"
                )
            ).all()
        ]


def test_serial_countersign_next_runner_carries_deadline():
    """回归（code-review 2026-10 P1-3）：串行会签换棒继承剩余档。

    档位属于节点等待而非任务行（与转办 carry 先例同源）——第 1 棒先办结
    不丢 SLA。修复前旧 Job 绑定已归档任务、到期按 skip 结案，后续全员
    无限时窗（完成侧与驳回侧共用 on_task_completed，同修）。"""
    engine, clock = _serial_mi_engine()
    instance_id = engine.runtime.start_process("serial_mi", start_user="u_s")
    t1 = engine.task.list_tasks(instance_id=instance_id, assignee="u_p1")[0]
    clock.advance(10)  # 第 1 棒用了 10 分钟
    engine.task.complete(t1.id, actor="u_p1")

    t2 = engine.task.list_tasks(instance_id=instance_id, assignee="u_p2")[0]
    jobs = _scheduled_jobs(engine, instance_id)
    assert (t2.id, "scheduled") in jobs, "新棒继承未触发档"
    assert all(j[1] != "scheduled" or j[0] != t1.id for j in jobs), "旧棒 Job 已 carry 走"
    assert t2.due_at is not None

    clock.advance(20)  # 节点级 30 分钟到点（第 1 棒吃掉 10 分钟）
    engine.jobs.tick()
    history = engine.history.task_history(instance_id)
    assert any(
        h.activity_code == "c" and h.assignee == "u_p2" and h.acted_by == "system:timeout"
        for h in history
    ), [(h.assignee, h.outcome, h.acted_by) for h in history]


def test_added_countersigner_gets_deadline():
    """回归（code-review 2026-10 P2-9）：加签克隆任务排 deadline——
    加签人不办不再无限期悬挂（新票人按并行同款独立满档）。"""
    from flowork.definitions.node import MultiInstance

    engine, clock = build_engine()
    deploy(
        engine,
        FlowBuilder("cs_add")
        .start()
        .user_task(
            "c",
            assignees=["'u_p1'", "'u_p2'"],
            multi_instance=MultiInstance(mode="parallel"),
            deadlines=[TaskDeadline(after_minutes=10, action="auto_approve")],
        )
        .end()
        .connect("start", "c")
        .connect("c", "end")
        .build(),
    )
    instance_id = engine.runtime.start_process("cs_add", start_user="u_s")
    p1 = engine.task.list_tasks(instance_id=instance_id, assignee="u_p1")[0]
    clock.advance(2)
    engine.task.add_countersigner(p1.id, users=["u_new"], actor="u_p1")
    new_task = engine.task.list_tasks(instance_id=instance_id, assignee="u_new")[0]
    assert new_task.due_at is not None, "加签任务物化 due_at"
    assert (new_task.id, "scheduled") in _scheduled_jobs(engine, instance_id)

    clock.advance(10)  # 加签后满档到点
    engine.jobs.tick()
    history = engine.history.task_history(instance_id)
    assert any(h.assignee == "u_new" and h.acted_by == "system:timeout" for h in history), [
        (h.assignee, h.outcome, h.acted_by) for h in history
    ]


def test_deadline_defers_while_consult_open_then_fires():
    """回归（code-review 2026-10 P2-12）：咨询软挂起期 deadline 推迟不消费。

    「时钟照走」语义＝意见交回后策略如期触发；修复前 Job 被消费、咨询后零时限。
    """
    from flowork.persistence.orm import JobRow

    engine, clock = build_engine()
    deploy(
        engine,
        FlowBuilder("consult_dl")
        .start()
        .user_task(
            "a",
            assignee="'u_a'",
            deadlines=[TaskDeadline(after_minutes=10, action="auto_approve")],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(),
    )
    instance_id = engine.runtime.start_process("consult_dl", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    opinions = engine.task.consult(task.id, users=["u_law"], actor="u_a")

    clock.advance(10)
    engine.jobs.tick()  # 咨询中：策略不可执行 → 推迟不消费
    with engine.session_factory() as session:
        job = session.scalars(select(JobRow).where(JobRow.instance_id == instance_id)).first()
        assert job is not None and job.state == "scheduled"
        assert job.result_payload.get("deferred") == "consult_open"
    assert engine.runtime.get_instance(instance_id).state == "running"

    engine.task.complete_consult(opinions[0].id, actor="u_law", opinion="agree")
    engine.jobs.tick()  # 意见交回：策略如期触发
    history = engine.history.task_history(instance_id)
    assert any(
        h.activity_code == "a" and h.outcome == "agree" and h.acted_by == "system:timeout"
        for h in history
    )
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_deadline_strategy_defers_on_frozen_instance_race_window():
    """回归（code-review 2026-10 P1-12）：策略执行面复验实例态——collect→claim
    竞态窗插队的 paused/suspended 推迟不消费（直接驱动 _apply_strategy 复现窗口）。"""
    from flowork.definitions.node import MultiInstance
    from flowork.persistence.orm import JobRow

    engine, clock = build_engine()
    deploy(
        engine,
        FlowBuilder("frozen_dl")
        .start()
        .user_task(
            "c",
            assignees=["'u_p1'"],
            multi_instance=MultiInstance(mode="parallel"),
            deadlines=[TaskDeadline(after_minutes=10, action="auto_approve")],
        )
        .end()
        .connect("start", "c")
        .connect("c", "end")
        .build(),
    )
    instance_id = engine.runtime.start_process("frozen_dl", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    clock.advance(10)
    engine.runtime.pause_process(instance_id, actor="u_ops")

    with engine.session_factory() as s:
        job_id = s.scalars(select(JobRow.id).where(JobRow.instance_id == instance_id)).first()

    def _fire(ctx):  # 绕过 collect 直接驱动策略执行面（复现 TOCTOU 窗口落点）
        job = ctx.session.get(JobRow, job_id)
        engine.jobs._apply_strategy(ctx, job)

    engine.execute(_fire)
    with engine.session_factory() as session:
        job = session.get(JobRow, job_id)
        assert job.state == "scheduled", "冻结期竞态窗：推迟不消费"
        assert job.result_payload.get("deferred") == "paused"
    assert engine.task.list_tasks(instance_id=instance_id, assignee="u_p1"), "任务未被代投"

    engine.runtime.resume_process(instance_id, actor="u_ops")
    engine.jobs.tick()
    history = engine.history.task_history(instance_id)
    assert any(h.assignee == "u_p1" and h.acted_by == "system:timeout" for h in history)
    _ = task
