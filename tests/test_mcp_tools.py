"""MCP 18 工具 dispatch 行为测试（openface-plugin-design.md §7）：传输层之下直接执行。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from flowork.api import (
    EngineConfig,
    FlowBuilder,
    FloworkError,
    ProcessEngine,
    ProcessVariable,
)
from flowork.contrib.mcp.catalog import dispatch
from flowork.definitions.definition import dump_definition


@pytest.fixture()
def engine() -> ProcessEngine:
    return ProcessEngine(EngineConfig())


def _simple_spec() -> dict[str, Any]:
    definition = (
        FlowBuilder("mcp_simple", "MCP 简单审批")
        .start()
        .user_task("manager", name="经理审批", assignee="'u_manager'")
        .end()
        .connect("start", "manager")
        .connect("manager", "end")
        .build(variables=[ProcessVariable(name="amount", type="number")])
    )
    return dump_definition(definition)


def _candidate_spec() -> dict[str, Any]:
    definition = (
        FlowBuilder("mcp_cand", "MCP 候选认领")
        .start()
        .user_task("review", candidate_users=["'u_worker'"])
        .end()
        .connect("start", "review")
        .connect("review", "end")
        .build()
    )
    return dump_definition(definition)


def _wait_spec() -> dict[str, Any]:
    definition = (
        FlowBuilder("mcp_wait", "MCP 等回函")
        .start()
        .wait("w", message={"name": "sign_back"})
        .end()
        .connect("start", "w")
        .connect("w", "end")
        .build()
    )
    return dump_definition(definition)


def test_validate_spec_ok_and_bad(engine: ProcessEngine) -> None:
    ok = dispatch(engine, "validate_spec", {"spec": _simple_spec()})
    assert ok["ok"] is True and ok["issues"] == []

    bad = _simple_spec()
    bad["nodes"][1]["assignee"] = "role:legal"  # 人员位组引用＝设计期 ERROR
    result = dispatch(engine, "validate_spec", {"spec": bad})
    assert result["ok"] is False and result["issues"]


def test_dry_run_lists_steps(engine: ProcessEngine) -> None:
    result = dispatch(engine, "dry_run", {"spec": _simple_spec(), "variables": {"amount": 1}})
    assert result["blocked"] == []
    assert [step["node"] for step in result["steps"]] == ["start", "manager", "end"]


def test_deploy_verdict_and_list(engine: ProcessEngine) -> None:
    verdict = dispatch(engine, "deploy", {"spec": _simple_spec(), "actor": "u_ci"})
    assert verdict["code"] == "mcp_simple" and verdict["next_major"] == 1
    rows = dispatch(engine, "list_definitions", {})["items"]
    assert any(row["code"] == "mcp_simple" for row in rows)


def test_definition_drift(engine: ProcessEngine, tmp_path: Path) -> None:
    (tmp_path / "mcp_simple.json").write_text(
        json.dumps(_simple_spec(), ensure_ascii=False), encoding="utf-8"
    )
    report = dispatch(engine, "definition_drift", {"directory": str(tmp_path)})
    assert set(report) == {"undeployed", "extra_in_db", "stale_in_db"}
    assert any(row["code"] == "mcp_simple" for row in report["undeployed"])


def test_start_query_complete_chain(engine: ProcessEngine) -> None:
    dispatch(engine, "deploy", {"spec": _simple_spec(), "actor": "u_ci"})
    instance_id = dispatch(
        engine,
        "start_instance",
        {
            "code": "mcp_simple",
            "actor": "u_requester",
            "variables": {"amount": 1},
        },
    )["instance_id"]

    view = dispatch(engine, "query_instance", {"instance_id": instance_id})
    assert view["instance"]["state"] == "running" and view["variables"]["amount"] == 1

    tasks = dispatch(engine, "list_my_tasks", {"assignee": "u_manager"})
    assert tasks["total"] == 1 and tasks["items"][0]["instance_id"] == instance_id

    anticipate = dispatch(engine, "anticipate_task", {"task_id": tasks["items"][0]["id"]})
    assert anticipate["node_exit"] is True

    result = dispatch(
        engine,
        "complete_task",
        {"task_id": tasks["items"][0]["id"], "actor": "u_manager", "comment": "同意"},
    )
    assert result["instance_state"] == "completed"


def test_update_instance_variables(engine: ProcessEngine) -> None:
    dispatch(engine, "deploy", {"spec": _simple_spec(), "actor": "u_ci"})
    instance_id = dispatch(
        engine,
        "start_instance",
        {
            "code": "mcp_simple",
            "actor": "u_requester",
            "variables": {"amount": 1},
        },
    )["instance_id"]
    updated = dispatch(
        engine,
        "update_instance_variables",
        {"instance_id": instance_id, "updates": {"amount": 42}, "actor": "u_ops"},
    )
    assert updated["amount"] == 42


def test_publish_message_no_waiter_buffers(engine: ProcessEngine) -> None:
    result = dispatch(engine, "publish_message", {"name": "nobody_waiting"})
    assert result == {"name": "nobody_waiting", "matched": False}


def test_signal_instance_receipt(engine: ProcessEngine) -> None:
    dispatch(engine, "deploy", {"spec": _wait_spec(), "actor": "u_ci"})
    instance_id = dispatch(engine, "start_instance", {"code": "mcp_wait", "actor": "u_ai"})[
        "instance_id"
    ]
    receipt = dispatch(
        engine,
        "signal_instance",
        {"instance_id": instance_id, "name": "sign_back", "payload": {"signed": True}},
    )
    assert receipt["instance_id"] == instance_id


def test_pause_and_incident_tools(engine: ProcessEngine) -> None:
    dispatch(engine, "deploy", {"spec": _simple_spec(), "actor": "u_ci"})
    instance_id = dispatch(engine, "start_instance", {"code": "mcp_simple", "actor": "u_ai"})[
        "instance_id"
    ]
    dispatch(engine, "pause_instance", {"instance_id": instance_id, "actor": "u_admin"})
    view = dispatch(engine, "query_instance", {"instance_id": instance_id})
    assert view["instance"]["state"] == "paused"
    # 收尾批 6（拍板④）：pause/resume 对称——AI 暂停后可自恢复
    result = dispatch(engine, "resume_instance", {"instance_id": instance_id, "actor": "u_admin"})
    assert result == {"instance_id": instance_id, "state": "running"}
    view = dispatch(engine, "query_instance", {"instance_id": instance_id})
    assert view["instance"]["state"] == "running"

    assert dispatch(engine, "list_incidents", {})["items"] == []
    with pytest.raises(FloworkError):
        dispatch(engine, "resolve_incident", {"incident_id": "nope", "action": "resume"})


def test_unknown_tool_lookup_error(engine: ProcessEngine) -> None:
    with pytest.raises(LookupError):
        dispatch(engine, "no_such_tool", {})


def test_claim_task_lifecycle(engine: ProcessEngine) -> None:
    """认领链路（review P2-6：19 工具中此前零行为覆盖的两个之一）。"""
    dispatch(engine, "deploy", {"spec": _candidate_spec(), "actor": "u_ci"})
    dispatch(engine, "start_instance", {"code": "mcp_cand", "actor": "u_req"})
    pool = dispatch(engine, "list_my_tasks", {"assignee": "", "candidate": "u_worker"})
    assert pool["total"] == 1
    claimed = dispatch(
        engine, "claim_task", {"task_id": pool["items"][0]["id"], "user": "u_worker"}
    )
    assert claimed["assignee"] == "u_worker"
    result = dispatch(
        engine,
        "complete_task",
        {"task_id": pool["items"][0]["id"], "actor": "u_worker"},
    )
    assert result["instance_state"] == "completed"


def test_reject_task_targets_source(engine: ProcessEngine) -> None:
    """驳回目标透传（review P2-6 余一）：驳回后回到来源节点重建待办。"""
    dispatch(engine, "deploy", {"spec": _simple_spec(), "actor": "u_ci"})
    instance_id = dispatch(
        engine,
        "start_instance",
        {"code": "mcp_simple", "actor": "u_requester"},
    )["instance_id"]
    tasks = dispatch(engine, "list_my_tasks", {"assignee": "u_manager"})
    result = dispatch(
        engine,
        "reject_task",
        {"task_id": tasks["items"][0]["id"], "actor": "u_manager", "comment": "材料不全"},
    )
    assert result["instance_state"] == "running"
    assert engine.runtime.get_instance(instance_id).state == "running"
