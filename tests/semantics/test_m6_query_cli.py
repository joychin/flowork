"""M6 测试：查询分页 / 流转轨迹 / 历史分级 / 归档导出 / CLI 演示。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine

REPO = Path(__file__).parent.parent.parent


def _simple_engine(**config_kwargs) -> ProcessEngine:
    return ProcessEngine(EngineConfig(**config_kwargs) if config_kwargs else EngineConfig())


def _run_simple(engine: ProcessEngine) -> str:
    definition = (
        FlowBuilder("q")
        .start()
        .user_task("a", assignee="u_a")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("q", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a")
    return instance_id


def test_paged_tasks():
    engine = _simple_engine()
    _run_simple(engine)
    page = engine.history.paged_tasks(assignee="u_a", offset=0, limit=10)
    assert page.total == 1 and len(page.items) == 1
    assert page.items[0].activity_code == "a"


def test_trace_instance_contains_all_kinds():
    engine = _simple_engine()
    instance_id = _run_simple(engine)
    trace = engine.history.trace_instance(instance_id)
    kinds = {entry.kind for entry in trace}
    assert {"token", "task", "event"} <= kinds
    titles = [entry.title for entry in trace]
    assert any("a → finished" in title for title in titles)


def test_history_level_none_suppresses_events():
    engine = _simple_engine(history_level="none")
    instance_id = _run_simple(engine)
    assert engine.history.events(instance_id) == []
    assert engine.runtime.get_instance(instance_id).state == "completed"  # 流程不受影响


def test_history_level_activity_keeps_node_events_only():
    engine = _simple_engine(history_level="activity")
    instance_id = _run_simple(engine)
    names = [e.event_name for e in engine.history.events(instance_id)]
    assert "node_entered" in names
    assert "task_created" not in names


def test_archive_instance_exports_full_state():
    engine = _simple_engine()
    instance_id = _run_simple(engine)
    archive = engine.history.archive_instance(instance_id)
    assert archive["instance"]["id"] == instance_id
    assert archive["instance"]["state"] == "completed"
    assert any(t["activity_code"] == "a" for t in archive["tokens"])
    assert any(h["activity_code"] == "a" for h in archive["task_history"])
    assert archive["events"]
    import json

    json.dumps(archive, ensure_ascii=False)  # 可序列化


def test_archive_missing_instance_raises():
    engine = _simple_engine()
    from flowork.errors import InstanceNotFoundError

    with pytest.raises(InstanceNotFoundError):
        engine.history.archive_instance("no-such")


def test_cli_demo_full_runs():
    result = subprocess.run(
        [sys.executable, "-m", "flowork.cli.app", "demo-full"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "DoD 演示通过" in result.stdout
    assert "驳回" in result.stdout and "传阅" in result.stdout


def test_cli_validate_ok():
    result = subprocess.run(
        [sys.executable, "-m", "flowork.cli.app", "validate", "examples/01_simple_approval.json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )
    assert result.returncode == 0 and "校验通过" in result.stdout


def test_cli_validate_bad_template(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"code": "bad", "nodes": [], "flows": []}', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "flowork.cli.app", "validate", str(bad)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )
    assert result.returncode == 1  # 缺 start → 报错退出
