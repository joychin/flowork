"""M5 语义测试：子流程 / 超时策略与 Job 三级 / Incident 修复 / move_token / ServiceTask。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.node import TimeoutPolicy
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine


class FakeClock:
    """可推进的模拟时钟（超时测试确定性）。"""

    def __init__(self) -> None:
        self.now_value = datetime(2026, 9, 30, 9, 0, 0)

    def __call__(self) -> datetime:
        return self.now_value

    def advance_minutes(self, minutes: int) -> None:
        self.now_value += timedelta(minutes=minutes)


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def publish(engine: ProcessEngine, definition) -> None:
    engine.repository.deploy(definition)


# —— 子流程 ——


def _publish_parent_and_child(engine: ProcessEngine) -> None:
    child = (
        FlowBuilder("compliance_check", "合规检查")
        .start()
        .user_task("verify", assignee="'u_compliance'")
        .end()
        .connect("start", "verify")
        .connect("verify", "end")
        .build()
    )
    publish(engine, child)
    parent = (
        FlowBuilder("parent_flow", "主流程")
        .start()
        .user_task("apply", assignee="'u_maker'")
        .call_activity(
            "sub",
            name="合规子流程",
            process_code="compliance_check",
            input_mappings={"amount": "amount"},
            output_mappings={"compliance_result": "verdict"},
        )
        .end()
        .connect("start", "apply")
        .connect("apply", "sub")
        .connect("sub", "end")
        .build()
    )
    publish(engine, parent)


def test_call_activity_blocks_until_sub_completes(engine: ProcessEngine):
    """父流程在 CallActivity 等待；子流程任务办完 → 父流程恢复并完结。"""
    _publish_parent_and_child(engine)
    parent_id = engine.runtime.start_process(
        "parent_flow", start_user="u_maker", variables={"amount": 999}
    )

    apply_task = engine.task.list_tasks(instance_id=parent_id)[0]
    result = engine.task.complete(apply_task.id, actor="u_maker")

    # 子流程已启动：父实例 running，子任务存在
    assert result.instance_state == "running"
    sub_tasks = engine.task.list_tasks(instance_id=parent_id, state=None)
    assert sub_tasks == []  # 子任务在子实例上
    children = [i for i in engine.history.instances() if i.parent_id == parent_id]
    assert len(children) == 1 and children[0].state == "running"
    sub_task = engine.task.list_tasks(instance_id=children[0].id)[0]
    assert sub_task.assignee == "u_compliance"

    # 办完子任务 → 子实例完结 → 父流程恢复完结（输出映射生效）
    engine.task.complete(sub_task.id, actor="u_compliance", variables={"verdict": "pass"})
    assert engine.runtime.get_instance(children[0].id).state == "completed"
    assert engine.runtime.get_instance(parent_id).state == "completed"
    variables = engine.execute(
        lambda ctx: (
            ctx.session.get(
                __import__(
                    "flowork.persistence.orm", fromlist=["ProcessInstanceRow"]
                ).ProcessInstanceRow,
                parent_id,
            ).variables
        )
    )
    assert variables.get("compliance_result") == "pass"


# —— ServiceTask ——


def test_service_task_runs_handler(engine: ProcessEngine):
    engine.register_handler(
        "grade",
        lambda ctx, params: ctx.variables.update(
            {"grade": "A" if ctx.variables.get("amount", 0) > 100 else "B"}
        ),
    )
    flow = (
        FlowBuilder("svc")
        .start()
        .service_task("score", action="grade")
        .user_task("confirm", assignee="'u_user'")
        .end()
        .connect("start", "score")
        .connect("score", "confirm")
        .connect("confirm", "end")
        .build()
    )
    publish(engine, flow)
    instance_id = engine.runtime.start_process("svc", start_user="u_s", variables={"amount": 500})
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_user")
    assert engine.runtime.get_instance(instance_id).state == "completed"


# —— 超时策略（模拟时钟）——


def _timeout_engine(strategy: str) -> tuple[ProcessEngine, FakeClock]:
    clock = FakeClock()
    eng = ProcessEngine(EngineConfig(now=clock))

    flow = (
        FlowBuilder("to_" + strategy)
        .start()
        .user_task(
            "slow",
            assignee="'u_slow'",
            timeout=TimeoutPolicy(due_minutes=60, strategy=strategy),
        )
        .end()
        .connect("start", "slow")
        .connect("slow", "end")
        .build()
    )
    publish(eng, flow)
    return eng, clock


def test_timeout_auto_approve_completes_flow():
    eng, clock = _timeout_engine("auto_approve")
    instance_id = eng.runtime.start_process("to_auto_approve", start_user="u_s")
    assert eng.task.list_tasks(instance_id=instance_id) != []

    clock.advance_minutes(61)
    processed = eng.jobs.tick()
    assert processed == 1
    assert eng.runtime.get_instance(instance_id).state == "completed"  # 自动通过直达 end
    history = eng.history.task_history(instance_id)
    assert any(h.outcome == "agree" and h.acted_by == "system:timeout" for h in history)


def test_timeout_auto_reject_redirects():
    eng, clock = _timeout_engine("auto_reject")
    # 有来源节点可驳回：加一个前置节点
    flow = (
        FlowBuilder("to_ar")
        .start()
        .user_task("first", assignee="'u_first'")
        .user_task(
            "slow",
            assignee="'u_slow'",
            timeout=__import__(
                "flowork.definitions.node", fromlist=["TimeoutPolicy"]
            ).TimeoutPolicy(due_minutes=30, strategy="auto_reject"),
        )
        .end()
        .connect("start", "first")
        .connect("first", "slow")
        .connect("slow", "end")
        .build()
    )
    publish(eng, flow)
    instance_id = eng.runtime.start_process("to_ar", start_user="u_s")
    engine_first = eng.task.list_tasks(instance_id=instance_id)[0]
    eng.task.complete(engine_first.id, actor="u_first")

    clock.advance_minutes(31)
    eng.jobs.tick()
    # 超时自动驳回 → 回退到 first，u_first 重新待办
    tasks = [(t.activity_code, t.assignee) for t in eng.task.list_tasks(instance_id=instance_id)]
    assert tasks == [("first", "u_first")]


def test_timeout_notify_emits_event():
    eng, clock = _timeout_engine("notify")
    instance_id = eng.runtime.start_process("to_notify", start_user="u_s")
    clock.advance_minutes(61)
    eng.jobs.tick()
    events = [e.event_name for e in eng.history.events(instance_id)]
    assert "timeout_warned" in events
    assert eng.runtime.get_instance(instance_id).state == "running"  # 任务仍在


def test_timeout_not_due_yet_noop():
    eng, clock = _timeout_engine("notify")
    instance_id = eng.runtime.start_process("to_notify", start_user="u_s")
    clock.advance_minutes(10)
    assert eng.jobs.tick() == 0
    assert eng.task.list_tasks(instance_id=instance_id) != []


def test_job_strategy_error_suspends_with_incident(engine: ProcessEngine):
    """策略执行持续失败（路由缺变量）→ 退避重试 → dead_letter。"""
    clock = FakeClock()
    from flowork.engine.config import EngineConfig as _Cfg

    eng = ProcessEngine(_Cfg(now=clock))
    from flowork.definitions.builder import FlowBuilder
    from flowork.definitions.node import TimeoutPolicy
    from flowork.persistence.orm import JobRow

    flow = (
        FlowBuilder("dl")
        .start()
        .user_task(
            "t",
            assignee="'u_x'",
            timeout=TimeoutPolicy(due_minutes=10, strategy="auto_approve"),
        )
        .branch("route")
        .end()
        .connect("start", "t")
        .connect("t", "route")
        .flow("route", "end", condition="ghost == 1")
        .build()
    )
    publish(eng, flow)
    _instance_id = eng.runtime.start_process("dl", start_user="u_s")

    def _collect_jobs(ctx):
        from sqlalchemy import select

        return list(ctx.session.scalars(select(JobRow)))

    clock.advance_minutes(11)
    eng.jobs.tick()  # auto_approve → 公式缺 ghost 变量 → 技术故障（单轨语义，spec §8）
    instance = eng.runtime.get_instance(_instance_id)
    assert instance.state == "suspended"  # 命令失败挂起 + 故障单，替代死信耗尽路径
    incidents = eng.runtime.list_incidents(instance_id=_instance_id)
    assert incidents and incidents[0]["state"] == "open"
    assert "公式求值失败" in incidents[0]["message"]


def test_dead_letter_job_can_be_requeued(engine: ProcessEngine):
    """死信 Job 人工重派：状态重置、计数清零、可再次执行。"""
    from flowork.persistence.orm import JobRow

    def _make_dead(ctx):
        ctx.session.add(
            JobRow(
                id="job-dead-1",
                job_type="timer",
                state="dead_letter",
                run_at=datetime(2026, 9, 30, 8, 0, 0),
                retry_count=3,
                instance_id="fake-instance",
                payload={"strategy": "notify"},
            )
        )

    engine.execute(_make_dead)
    engine.jobs.requeue("job-dead-1")

    def _check(ctx):
        job = ctx.session.get(JobRow, "job-dead-1")
        assert job is not None
        return job.state, job.retry_count

    assert engine.execute(_check) == ("scheduled", 0)
    with pytest.raises(ValueError):
        engine.jobs.requeue("no-such-job")


def test_mappings_cel_expressions(engine: ProcessEngine):
    """映射值 CEL（spec §4.2）：输入侧运算表达式、输出侧字符串拼接。"""
    child = (
        FlowBuilder("compliance_expr", "合规表达式")
        .start()
        .user_task("verify", assignee="'u_compliance'")
        .end()
        .connect("start", "verify")
        .connect("verify", "end")
        .build()
    )
    publish(engine, child)
    parent = (
        FlowBuilder("parent_expr", "主流程表达式")
        .start()
        .call_activity(
            "sub",
            process_code="compliance_expr",
            input_mappings={"doubled": "amount * 2", "who": "initiator"},
            output_mappings={"verdict_line": '"verdict:" + verdict'},
        )
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    publish(engine, parent)
    from flowork.persistence.orm import ProcessInstanceRow

    instance_id = engine.runtime.start_process(
        "parent_expr", start_user="u_maker", variables={"amount": 21}
    )

    def _child() -> ProcessInstanceRow:
        return engine.execute(
            lambda ctx: next(
                row
                for row in ctx.session.query(ProcessInstanceRow).all()
                if row.parent_id == instance_id
            )
        )

    child = _child()
    assert child.variables["doubled"] == 42  # 求值后的输入映射
    assert child.variables["who"] == "u_maker"
    task = engine.task.list_tasks(instance_id=child.id)[0]
    engine.task.complete(task.id, actor="u_compliance", variables={"verdict": "pass"})
    assert engine.runtime.get_instance(instance_id).state == "completed"

    def _parent_vars() -> dict:
        return engine.execute(
            lambda ctx: ctx.session.get(ProcessInstanceRow, instance_id).variables
        )

    assert _parent_vars()["verdict_line"] == "verdict:pass"


def test_output_mapping_missing_bare_ident_is_none(engine: ProcessEngine):
    """裸标识符直传缺键 → None（管理员修复直达终态等场景，旧 .get 语义）。"""
    child = (
        FlowBuilder("compliance_silent", "合规静默")
        .start()
        .user_task("verify", assignee="'u_compliance'")
        .end()
        .connect("start", "verify")
        .connect("verify", "end")
        .build()
    )
    publish(engine, child)
    parent = (
        FlowBuilder("parent_silent", "主流程静默")
        .start()
        .call_activity(
            "sub",
            process_code="compliance_silent",
            output_mappings={"result": "never_set"},
        )
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    publish(engine, parent)
    from flowork.persistence.orm import ProcessInstanceRow

    instance_id = engine.runtime.start_process("parent_silent", start_user="u_maker")

    def _child_id() -> str:
        return engine.execute(
            lambda ctx: next(
                row.id
                for row in ctx.session.query(ProcessInstanceRow).all()
                if row.parent_id == instance_id
            )
        )

    child_id = _child_id()
    verify_token = next(t for t in engine.history.tokens(child_id) if t.activity_code == "verify")
    engine.runtime.move_token(verify_token.id, "end", actor="admin")  # 管理员直达终态
    assert engine.runtime.get_instance(instance_id).state == "completed"

    def _parent_vars() -> dict:
        return engine.execute(
            lambda ctx: ctx.session.get(ProcessInstanceRow, instance_id).variables
        )

    assert _parent_vars()["result"] is None


def test_input_mapping_expression_error_suspends():
    """映射表达式求值错 → 技术故障（挂起+故障单，spec §8）。"""
    from flowork.engine.kernel import EngineExecutionError

    child = (
        FlowBuilder("compliance_bad", "合规坏映射").start().end().connect("start", "end").build()
    )
    engine = ProcessEngine()
    publish(engine, child)
    parent = (
        FlowBuilder("parent_bad", "主流程坏映射")
        .start()
        .call_activity("sub", process_code="compliance_bad", input_mappings={"x": "nosuchvar + 1"})
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    publish(engine, parent)
    with pytest.raises(EngineExecutionError, match="子流程输入映射求值失败"):
        engine.runtime.start_process("parent_bad", start_user="u_maker", variables={})
