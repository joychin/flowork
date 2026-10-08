"""CLI instance 子命令组（变量 I/O 批）：vars / vars-set / var-history 三面投影。"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from flowork.cli.app import app
from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine

runner = CliRunner()


@pytest.fixture
def world(tmp_path):
    """临时库：部署带声明变量的流程并发起一个实例，返回 (db, instance_id)。"""
    db = tmp_path / "cli.db"
    engine = ProcessEngine(EngineConfig(db_file=db))
    definition = (
        FlowBuilder("cli_io")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    definition.variables = [ProcessVariable(name="amount", type="number", sample=100)]
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process(
        "cli_io", start_user="u_s", variables={"amount": 100}
    )
    engine.close()
    return db, instance_id


def test_vars_reads_full_set(world):
    db, instance_id = world
    result = runner.invoke(app, ["instance", "vars", instance_id, "--db", str(db)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["amount"] == 100


def test_vars_set_updates_and_reports(world):
    db, instance_id = world
    result = runner.invoke(
        app,
        [
            "instance",
            "vars-set",
            instance_id,
            "amount=999",
            "--db",
            str(db),
            "--as",
            "u_api",
            "--reason",
            "风控刷新",
        ],
    )
    assert result.exit_code == 0, result.stdout
    verify = runner.invoke(app, ["instance", "vars", instance_id, "--db", str(db)])
    assert json.loads(verify.stdout)["amount"] == 999


def test_vars_set_rejects_terminal_or_undeclared(world):
    db, instance_id = world
    # 未声明键 → 拒收（退出码 1，不抛栈）
    bad = runner.invoke(
        app, ["instance", "vars-set", instance_id, "nope=1", "--db", str(db), "--as", "u_api"]
    )
    assert bad.exit_code == 1
    assert "拒绝" in bad.stdout
    # 未知实例 → 拒收
    missing = runner.invoke(
        app, ["instance", "vars-set", "ghost", "amount=1", "--db", str(db), "--as", "u_api"]
    )
    assert missing.exit_code == 1


def test_var_history_table(world):
    db, instance_id = world
    runner.invoke(
        app, ["instance", "vars-set", instance_id, "amount=777", "--db", str(db), "--as", "u_api"]
    )
    result = runner.invoke(
        app, ["instance", "var-history", instance_id, "--name", "amount", "--db", str(db)]
    )
    assert result.exit_code == 0
    assert "external_update" in result.stdout and "u_api" in result.stdout
