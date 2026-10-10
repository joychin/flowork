"""M1 语义测试：执行内核主干（发起→审批→结束 / 会签 / 幂等 / actor / 快照 / 取消）。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm.exc import StaleDataError

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import load_definition_file
from flowork.definitions.node import MultiInstance
from flowork.engine.engine import ProcessEngine
from flowork.errors import TaskNotAvailableError
from tests.conftest import EXAMPLES_DIR


@pytest.fixture
def engine() -> ProcessEngine:
    eng = ProcessEngine()
    publish_example(eng, EXAMPLES_DIR / "01_simple_approval.json")
    return eng


def publish_example(engine: ProcessEngine, path) -> None:
    from flowork.runtime.registry import create_registry

    definition = load_definition_file(path, create_registry())
    engine.repository.deploy(definition)


def test_start_to_end_full_chain(engine: ProcessEngine):
    """发起→经理→财务→结束：任务链、实例状态、事件序列、令牌轨迹全部正确。"""
    instance_id = engine.runtime.start_process(
        "simple_approval", start_user="u_zhang", variables={"amount": 500}
    )
    assert engine.runtime.get_instance(instance_id).state == "running"

    manager_task = engine.task.list_tasks(instance_id=instance_id, assignee="u_manager")
    assert len(manager_task) == 1 and manager_task[0].activity_code == "manager"

    result = engine.task.complete(manager_task[0].id, actor="u_manager", comment="同意")
    assert result.instance_state == "running"
    assert [t.activity_code for t in result.next_tasks] == ["finance"]

    finance_task = engine.task.list_tasks(instance_id=instance_id, assignee="u_finance")
    result2 = engine.task.complete(finance_task[0].id, actor="u_finance")
    assert result2.instance_state == "completed"

    event_names = [e.event_name for e in engine.history.events(instance_id)]
    assert event_names[0] == "process_started"
    assert "task_created" in event_names and "task_completed" in event_names
    assert event_names[-1] in ("process_completed", "node_exited")

    tokens = engine.history.tokens(instance_id)
    assert [t.activity_code for t in tokens] == ["start", "manager", "finance", "end"]
    assert all(t.state == "finished" for t in tokens)

    assert engine.task.list_tasks(instance_id=instance_id) == []  # 活表已清空


def test_parallel_countersign_all_by_default(engine: ProcessEngine):
    """并签默认 all：一人办理后节点仍运行，第二人办理后才完成。"""
    definition = (
        FlowBuilder("countersign")
        .start()
        .user_task(
            "audit",
            "会签",
            assignees=["'u_a'", "'u_b'"],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "audit")
        .connect("audit", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("countersign", start_user="u_starter")

    tasks = {t.assignee: t for t in engine.task.list_tasks(instance_id=instance_id)}
    assert set(tasks) == {"u_a", "u_b"}

    result = engine.task.complete(tasks["u_a"].id, actor="u_a")
    assert result.instance_state == "running"  # 未到 all
    assert engine.task.list_tasks(instance_id=instance_id, assignee="u_b") != []

    result2 = engine.task.complete(tasks["u_b"].id, actor="u_b")
    assert result2.instance_state == "completed"

    token = next(t for t in engine.history.tokens(instance_id) if t.activity_code == "audit")
    assert (token.item_count, token.approve_count, token.approval_exit) == (2, 2, "agree")


def test_idempotent_complete(engine: ProcessEngine):
    """同幂等键重复提交：返回首次结果，无副作用（历史只有一条）。"""
    instance_id = engine.runtime.start_process("simple_approval", start_user="u_zhang")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    first = engine.task.complete(task.id, actor="u_manager", idempotency_key="op-001")
    second = engine.task.complete(task.id, actor="u_manager", idempotency_key="op-001")
    assert first.task_id == second.task_id and second.outcome == "agree"
    history = [h for h in engine.history.task_history(instance_id) if h.task_id == task.id]
    assert len(history) == 1
    assert engine.task.list_tasks(instance_id=instance_id)[0].assignee == "u_finance"


def test_double_complete_without_key_raises(engine: ProcessEngine):
    """任务已完成后再次提交（无幂等键）必须报错。"""
    instance_id = engine.runtime.start_process("simple_approval", start_user="u_zhang")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_manager")
    with pytest.raises(TaskNotAvailableError):
        engine.task.complete(task.id, actor="u_manager")


def test_actor_attribution(engine: ProcessEngine):
    """actor 归因：办理人字符串（人/AI 一律裸 id）原样落历史。"""
    instance_id = engine.runtime.start_process("simple_approval", start_user="u_zhang")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="bot-001", comment="AI 预审通过")
    history = engine.history.task_history(instance_id)[0]
    assert history.acted_by == "bot-001"


def test_variables_merge_on_complete(engine: ProcessEngine):
    """提交时的变量合并进实例变量。"""
    instance_id = engine.runtime.start_process("simple_approval", start_user="u_zhang")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_manager", variables={"level": "L2"})
    events = [e.payload for e in engine.history.events(instance_id)]
    assert any(p.get("outcome") == "agree" for p in events)
    finance_task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(finance_task.id, actor="u_finance")
    # 变量保存在实例行（通过命令读取）
    from flowork.persistence.orm import ProcessInstanceRow

    state = engine.execute(lambda ctx: ctx.session.get(ProcessInstanceRow, instance_id).variables)
    assert state.get("level") == "L2"


def test_definition_snapshot_isolation(engine: ProcessEngine):
    """在途实例使用发起时快照：定义后续演进不影响已发起实例。"""
    instance_id = engine.runtime.start_process("simple_approval", start_user="u_zhang")
    # 定义演进：v2 更换财务审批人
    revised = (
        FlowBuilder("simple_approval", "串行审批 v2")
        .start("start", "发起")
        .user_task("manager", "经理审批", assignee="'u_manager'")
        .user_task("finance", "财务复核", assignee="'u_new_finance'")
        .end("end", "结束")
        .connect("start", "manager")
        .connect("manager", "finance")
        .connect("finance", "end")
        .build()
    )
    engine.repository.deploy(revised)

    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_manager")
    # 实例仍走快照中的 u_finance
    remaining = engine.task.list_tasks(instance_id=instance_id)
    assert [t.assignee for t in remaining] == ["u_finance"]


def test_cancel_process_revokes_tasks(engine: ProcessEngine):
    """取消实例：待办撤销（历史 outcome=cancelled）、实例 cancelled。"""
    instance_id = engine.runtime.start_process("simple_approval", start_user="u_zhang")
    engine.runtime.cancel_process(instance_id, actor="u_zhang", reason="填错了")
    assert engine.runtime.get_instance(instance_id).state == "cancelled"
    assert engine.task.list_tasks(instance_id=instance_id) == []
    outcomes = [h.outcome for h in engine.history.task_history(instance_id)]
    assert "cancelled" in outcomes
    tokens = engine.history.tokens(instance_id)
    assert any(t.state == "cancelled" for t in tokens)


def test_optimistic_lock_retry(engine: ProcessEngine, monkeypatch: pytest.MonkeyPatch):
    """乐观锁冲突自动重试：第一次抛 StaleDataError 后重跑成功。"""
    calls = {"n": 0}
    original = engine.session_factory

    def flaky_factory():
        return original()

    import flowork.engine.ops as ops

    real_run_once = ops._run_once

    def flaky_run_once(eng, command):
        calls["n"] += 1
        if calls["n"] == 1:
            raise StaleDataError("UPDATE expected 1 row; 0 matched")
        return real_run_once(eng, command)

    monkeypatch.setattr(ops, "_run_once", flaky_run_once)
    _ = flaky_factory
    result = engine.execute(lambda ctx: "ok")  # type: ignore[arg-type, return-value]
    assert result == "ok" and calls["n"] == 2


def test_engine_core_has_no_kind_branches():
    """硬不变量：引擎核心包不得出现节点 kind 字面量分支。"""
    import re
    from pathlib import Path

    engine_dir = Path(__file__).parent.parent.parent / "flowork" / "engine"
    kinds = "start|end|user_task|service_task|call_activity|cc_task|branch"
    pattern = re.compile(r'(==\s*"|!=\s*")(' + kinds + ')"')
    offenders = []
    for path in engine_dir.glob("*.py"):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and "test" not in path.name:
                offenders.append(f"{path.name}:{line_no}: {line.strip()}")
    assert offenders == [], f"引擎核心出现 kind 分支：\n{chr(10).join(offenders)}"
