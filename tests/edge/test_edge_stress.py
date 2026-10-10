"""边界压测：持久化跨引擎可见性 / 并发抢占 / 沙箱逃逸 / Unicode / 深链 / 超时竞争 / 清理级联。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.errors import TaskNotAvailableError, UnknownNodeKind
from flowork.expr.cel import FormulaError, evaluate_formula


def _linear(code: str, assignee: str = "'u_a'") -> FlowBuilder:
    return (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee=assignee)
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )


# —— 持久化：文件库跨引擎可见 ——


def test_file_db_shared_between_engines(tmp_path: Path):
    """引擎 A 写、引擎 B 读：文件库真实持久化（非内存共享）。"""
    db = tmp_path / "shared.db"
    definition = _linear("shared_flow").build()
    engine_a = ProcessEngine(EngineConfig(db_file=db))
    engine_a.repository.deploy(definition)
    instance_id = engine_a.runtime.start_process("shared_flow", start_user="u_s")
    engine_a.close()

    engine_b = ProcessEngine(EngineConfig(db_file=db))
    tasks = engine_b.task.list_tasks(instance_id=instance_id)
    assert [(t.activity_code, t.assignee) for t in tasks] == [("a", "u_a")]
    engine_b.task.complete(tasks[0].id, actor="u_a")
    assert engine_b.runtime.get_instance(instance_id).state == "completed"
    engine_b.close()

    engine_c = ProcessEngine(EngineConfig(db_file=db))
    assert engine_c.runtime.get_instance(instance_id).state == "completed"
    engine_c.close()


# —— 并发竞争 ——


def test_double_complete_race_one_wins(tmp_path: Path):
    """同任务并发双提交：恰一个成功，另一侧明确报错（活表状态守卫）。"""
    db = tmp_path / "race.db"
    engine = ProcessEngine(EngineConfig(db_file=db))
    engine.repository.deploy(_linear("race").build())
    instance_id = engine.runtime.start_process("race", start_user="u_s")

    # 模拟并发：两个会话各自读到 ready 后先后提交（引擎单进程内以命令为界）
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a")
    with pytest.raises(TaskNotAvailableError):
        engine.task.complete(task.id, actor="u_a")  # 后到者必败
    outcomes = [
        h.outcome for h in engine.history.task_history(instance_id) if h.activity_code == "a"
    ]
    assert outcomes.count("agree") == 1
    engine.close()


def test_two_engines_same_file_same_task(tmp_path: Path):
    """双引擎同库并发办同一任务：先提交者胜，后者收到不可办理。"""
    db = tmp_path / "two.db"
    setup = ProcessEngine(EngineConfig(db_file=db))
    setup.repository.deploy(_linear("two").build())
    instance_id = setup.runtime.start_process("two", start_user="u_s")
    task = setup.task.list_tasks(instance_id=instance_id)[0]
    setup.close()

    engine_x = ProcessEngine(EngineConfig(db_file=db))
    engine_y = ProcessEngine(EngineConfig(db_file=db))
    engine_x.task.complete(task.id, actor="u_a")
    with pytest.raises(TaskNotAvailableError):
        engine_y.task.complete(task.id, actor="u_a")
    assert engine_y.runtime.get_instance(instance_id).state == "completed"
    engine_x.close()
    engine_y.close()


# —— 公式（CEL）安全面：语法层直接拒绝 + 未注册函数求值期拒绝 ——


@pytest.mark.parametrize(
    "formula",
    [
        "__import__('os').system('echo pwned')",  # CEL 无此语法 → 解析期拒绝
        "exec('import os')",
        "eval('1+1')",
        "().__class__.__bases__[0].__subclasses__()",
        "[x for x in (1, 2)]",
        "open('/etc/passwd')",  # 未注册函数 → 求值期拒绝
    ],
)
def test_formula_sandbox_escape_attempts(formula):
    with pytest.raises(FormulaError):
        evaluate_formula(formula, {})


def test_formula_length_limit():
    with pytest.raises(FormulaError):
        evaluate_formula("true && " * 800 + "true", {})


# —— Unicode ——


def test_unicode_flow_end_to_end():
    """中文节点名/意见/传阅标题全链路 + 归档可序列化。"""
    engine = ProcessEngine()
    definition = (
        FlowBuilder("中文流程", "报销——差旅费")
        .start("发起", "提交申请")
        .user_task("主管审批", assignee="'张三'")
        .cc_task("抄送财务", recipients=["'李四'"])
        .end("结束", "归档")
        .connect("发起", "主管审批")
        .connect("主管审批", "抄送财务")
        .connect("抄送财务", "结束")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process(
        "中文流程", start_user="王五", variables={"金额": 1200}
    )
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    assert task.name == "主管审批"
    engine.task.complete(task.id, actor="张三", comment="同意——注意留存发票")
    assert engine.runtime.get_instance(instance_id).state == "completed"
    cc = engine.task.list_cc_tasks(recipient="李四")
    assert len(cc) == 1 and cc[0].title == "抄送财务"
    archive = engine.history.archive_instance(instance_id)
    import json

    json.dumps(archive, ensure_ascii=False)  # Unicode 归档可序列化
    trace = engine.history.trace_instance(instance_id)
    assert any("主管审批" in entry.title for entry in trace)


# —— 深链流程 ——


def test_deep_chain_50_nodes():
    """50 节点串行链：全程推进无死循环误伤，事件完整。"""
    engine = ProcessEngine()
    builder = FlowBuilder("deep").start()
    previous = "start"
    for index in range(50):
        code = f"n{index:02d}"
        builder.user_task(code, assignee=f"'u_{index:02d}'")
        builder.connect(previous, code)
        previous = code
    builder.end().connect(previous, "end")
    definition = builder.build()
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("deep", start_user="u_s")
    for _ in range(50):
        tasks = engine.task.list_tasks(instance_id=instance_id)
        assert tasks, "链未走完就断了"
        engine.task.complete(tasks[0].id, actor=tasks[0].assignee)
    assert engine.runtime.get_instance(instance_id).state == "completed"
    tokens = engine.history.tokens(instance_id)
    assert len(tokens) == 52  # start + 50 + end


# —— 超时与人工竞争 ——


def test_timeout_job_skipped_when_task_done_early():
    """人工先办完 → 到期 Job 跳过（done + skipped），不重试不死信。"""
    from flowork.definitions.node import TimeoutPolicy
    from flowork.persistence.orm import JobRow

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime(2026, 9, 30, 9, 0, 0)

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    engine = ProcessEngine(EngineConfig(now=clock))
    definition = (
        FlowBuilder("compete")
        .start()
        .user_task(
            "t",
            assignee="'u_a'",
            timeout=TimeoutPolicy(due_minutes=30, strategy="auto_approve"),
        )
        .end()
        .connect("start", "t")
        .connect("t", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("compete", start_user="u_s")

    # 人工在第 20 分钟办完
    clock.value += timedelta(minutes=20)
    engine.task.complete(engine.task.list_tasks(instance_id=instance_id)[0].id, actor="u_a")
    assert engine.runtime.get_instance(instance_id).state == "completed"

    clock.value += timedelta(minutes=15)  # 超时点已过
    processed = engine.jobs.tick()
    assert processed == 1

    def _job(ctx):
        from sqlalchemy import select

        return ctx.session.scalars(select(JobRow)).first()

    job = engine.execute(_job)
    assert job is not None
    assert job.state == "done" and job.result_payload.get("skipped")
    assert job.retry_count == 0  # 没有进入重试


# —— 删除级联 ——


def test_remove_instance_cascades_jobs_links_incidents():
    """remove_instance：身份关联/Job/Incident 全部清理，jobs.tick 不再爆炸。"""
    from flowork.definitions.node import TimeoutPolicy
    from flowork.persistence.orm import IdentityLinkRow, IncidentRow, JobRow

    engine = ProcessEngine()
    definition = (
        FlowBuilder("purge")
        .start()
        .user_task(
            "t",
            candidate_users=["'u_x'", "'u_y'"],
            timeout=TimeoutPolicy(due_minutes=30, strategy="auto_approve"),
        )
        .end()
        .connect("start", "t")
        .connect("t", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("purge", start_user="u_s")

    def _counts(ctx):
        return (
            ctx.session.query(JobRow).filter(JobRow.instance_id == instance_id).count(),
            ctx.session.query(IdentityLinkRow).count(),
            ctx.session.query(IncidentRow).filter(IncidentRow.instance_id == instance_id).count(),
        )

    before = engine.execute(_counts)
    assert before[0] == 1 and before[1] == 2  # 超时 Job 已排 + 两个候选关联

    engine.task.remove_instance(instance_id, actor="admin")
    after = engine.execute(_counts)
    assert after == (0, 0, 0)  # 全部级联清理

    engine.jobs.tick()  # 无孤儿任务可炸


# —— 未知 kind 加载 ——


def test_unknown_kind_at_load_clear_error():
    import json

    payload = {
        "code": "bad_kind",
        "nodes": [
            {"code": "s", "kind": "start"},
            {"code": "x", "kind": "no_such_kind"},
            {"code": "e", "kind": "end"},
        ],
        "flows": [
            {"source": "s", "target": "x"},
            {"source": "x", "target": "e"},
        ],
    }
    engine = ProcessEngine()
    with pytest.raises(UnknownNodeKind):
        engine.execute(
            lambda ctx: __import__(
                "flowork.definitions.definition", fromlist=["load_definition"]
            ).load_definition(json.dumps(payload), engine.config.node_registry)
        )


# —— 分页 ——


def test_paged_tasks_pagination():
    engine = ProcessEngine()
    definition = (
        FlowBuilder("page")
        .start()
        .user_task("a", assignees=["'u1'", "'u2'", "'u3'"])
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("page", start_user="u_s")
    for assignee in ("u1", "u2"):
        engine.task.complete(
            engine.task.list_tasks(instance_id=instance_id, assignee=assignee)[0].id,
            actor=assignee,
        )
    page = engine.history.paged_tasks(offset=0, limit=1)
    assert page.total >= 2 and len(page.items) == 1
    page2 = engine.history.paged_tasks(offset=1, limit=1)
    assert page2.items[0].id != page.items[0].id
