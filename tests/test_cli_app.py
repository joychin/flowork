"""CLI app 级命令包装层：init / validate / deploy / retire（CliRunner 黑盒）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from flowork.cli.app import app
from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import dump_definition

runner = CliRunner()
EXAMPLES = Path(__file__).parent.parent / "examples"


def _flow_v1():
    return (
        FlowBuilder("cli_deploy")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )


def _flow_v2_breaking():
    """删节点 b：已发布图上移除节点＝破坏性（R 规则族），版本应跳 major。"""
    return (
        FlowBuilder("cli_deploy")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )


def _write(path: Path, definition) -> str:
    path.write_text(
        json.dumps(dump_definition(definition), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return str(path)


@pytest.fixture
def simple(tmp_path) -> Path:
    return tmp_path / "simple.json"


def test_init_creates_database(tmp_path):
    db = tmp_path / "init.db"
    result = runner.invoke(app, ["init", "--db", str(db)])
    assert result.exit_code == 0 and db.exists()


def test_validate_passes_example():
    result = runner.invoke(app, ["validate", str(EXAMPLES / "01_simple_approval.json")])
    assert result.exit_code == 0
    assert "校验通过" in result.stdout


def test_validate_rejects_invalid(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"code": "bad"}', encoding="utf-8")
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code == 1


def test_deploy_first_publish_then_idempotent(tmp_path, simple):
    db = tmp_path / "d.db"
    first = runner.invoke(app, ["deploy", _write(simple, _flow_v1()), "--db", str(db)])
    assert first.exit_code == 0
    assert "发布为 v1.1" in first.stdout

    again = runner.invoke(app, ["deploy", str(simple), "--db", str(db)])
    assert again.exit_code == 0
    assert "幂等去重" in again.stdout


def test_deploy_breaking_change_bumps_major(tmp_path, simple):
    db = tmp_path / "d.db"
    runner.invoke(app, ["deploy", _write(simple, _flow_v1()), "--db", str(db)])
    result = runner.invoke(app, ["deploy", _write(simple, _flow_v2_breaking()), "--db", str(db)])
    assert result.exit_code == 0
    assert "发布为 v2.1" in result.stdout
    assert "破坏性" in result.stdout


def test_deploy_rejects_invalid_template(tmp_path):
    db = tmp_path / "d.db"
    bad = tmp_path / "bad.json"
    bad.write_text('{"code": "bad"}', encoding="utf-8")
    result = runner.invoke(app, ["deploy", str(bad), "--db", str(db)])
    assert result.exit_code == 1
    assert "deploy 失败" in result.stdout


def test_retire_after_deploy(tmp_path, simple):
    db = tmp_path / "d.db"
    runner.invoke(app, ["deploy", _write(simple, _flow_v1()), "--db", str(db)])
    result = runner.invoke(app, ["retire", "cli_deploy", "--db", str(db)])
    assert result.exit_code == 0
    assert "已 retire" in result.stdout


def test_task_claimable_lists_candidate_group_member(tmp_path):
    """候选待办查询（A 批）：具体人直接命中；组引用须给 --org 才展开。

    来源 2026-10-09：候选关联此前不经任何公开面暴露，人找不到可认领入口。
    """
    db = tmp_path / "claim.db"
    org = tmp_path / "org.yaml"
    org.write_text(
        "users:\n"
        "  u_fin: {roles: [finance]}\n"
        "  u_other: {}\n"
        "departments: {}\n"
        "delegations: []\n"
        "admins: [u_admin]\n",
        encoding="utf-8",
    )
    flow = (
        FlowBuilder("cli_candidate")
        .start()
        .user_task("pick", "候选任务", candidate_groups=["'role:finance'"])
        .end()
        .connect("start", "pick")
        .connect("pick", "end")
        .build()
    )
    runner.invoke(app, ["deploy", _write(tmp_path / "cand.json", flow), "--db", str(db)])

    from flowork.api import EngineConfig, ProcessEngine

    engine = ProcessEngine(EngineConfig(db_file=str(db)))
    engine.runtime.start_process("cli_candidate", start_user="u_other")
    engine.close()

    hit = runner.invoke(app, ["task", "claimable", "u_fin", "--db", str(db), "--org", str(org)])
    assert hit.exit_code == 0 and "候选任务" in hit.stdout

    miss = runner.invoke(app, ["task", "claimable", "u_other", "--db", str(db), "--org", str(org)])
    assert "没有可认领" in miss.stdout


def test_task_claimable_rejects_bad_org_file(tmp_path):
    """目录 YAML 不存在：报错退出而非静默答「没有待办」（防静默错答案）。"""
    result = runner.invoke(
        app, ["task", "claimable", "u_x", "--db", str(tmp_path / "x.db"), "--org", "/nope.yaml"]
    )
    assert result.exit_code == 1
