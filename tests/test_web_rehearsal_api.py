"""排练与场景 web 面（批 3/批 4）：端点行为 + 场景存储。

排练＝人点一步（步单快照：待办/已办/路由/变量/等待点）；场景＝脚本 CRUD
与运行（列表带最近红绿、运行记结论、草稿校验门槛同 simulate）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowork.contrib.web.app import create_app

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(db_file=tmp_path / "reh.db", seed=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def spec() -> dict:
    return json.loads((EXAMPLES / "04_expense_claim.json").read_text(encoding="utf-8"))


def test_rehearsal_start_step_and_state(client: TestClient, spec: dict) -> None:
    """排练三端点：启动给步单，步进换待办，快照可重取。"""
    resp = client.post(
        "/api/definitions/expense/rehearsal",
        json={"spec": spec, "variables": {"amount": 100}, "start_user": "u_zhang"},
    )
    assert resp.status_code == 200, resp.text
    state = resp.json()
    assert state["instance_id"]
    assert [t["activity_code"] for t in state["tasks"]] == ["submit"]
    task_id = state["tasks"][0]["id"]
    step = client.post(
        f"/api/rehearsals/{state['instance_id']}/step",
        json={"task_id": task_id, "action": "complete", "comment": "提交"},
    )
    assert step.status_code == 200, step.text
    assert [t["activity_code"] for t in step.json()["tasks"]] == ["manager"]
    again = client.get(f"/api/rehearsals/{state['instance_id']}")
    assert again.status_code == 200
    assert again.json()["instance_id"] == state["instance_id"]


def test_rehearsal_validation_gate(client: TestClient) -> None:
    """模拟前强制设计期校验：ERROR 拦 422（与 simulate 同门槛）。"""
    broken = {
        "code": "broken",
        "name": "坏定义",
        "nodes": [{"code": "n1", "kind": "user_task", "assignee": "'u'"}],
        "flows": [{"source": "n1", "target": "不存在"}],
    }
    resp = client.post("/api/definitions/broken/rehearsal", json={"spec": broken})
    assert resp.status_code == 422


def test_rehearsal_unknown_instance_404(client: TestClient) -> None:
    assert client.get("/api/rehearsals/nope").status_code == 404


def test_scenario_crud_and_run(client: TestClient, spec: dict) -> None:
    """场景保存/列表/取/跑/删：运行结论写回列表（红绿可预览）。"""
    script = {
        "name": "小额直通",
        "start_user": "u_zhang",
        "variables": {"amount": 100},
        "steps": [
            {"node": "submit", "by": "u_zhang", "action": "complete"},
            {"node": "manager", "by": "u_manager", "action": "complete"},
        ],
        "assertions": [{"type": "final_state", "equals": "completed"}],
    }
    # 草稿入库存草稿（场景运行取草稿）
    assert client.put("/api/definitions/expense", json=spec).status_code == 200
    saved = client.post(
        "/api/definitions/expense/scenarios", json={"name": "小额直通", "script": script}
    )
    assert saved.status_code == 200, saved.text
    scenario_id = saved.json()["id"]
    listing = client.get("/api/definitions/expense/scenarios").json()
    assert [row["name"] for row in listing] == ["小额直通"]
    assert listing[0]["last_ok"] is None  # 未跑过

    run = client.post(f"/api/scenarios/{scenario_id}/run", json={})
    assert run.status_code == 200, run.text
    report = run.json()
    assert report["ok"] is True
    assert report["assertions"][0]["ok"] is True
    assert report["coverage"]["nodes"]["total"] > 0

    listing = client.get("/api/definitions/expense/scenarios").json()
    assert listing[0]["last_ok"] is True
    assert "全绿" in listing[0]["last_summary"]

    detail = client.get(f"/api/scenarios/{scenario_id}").json()
    assert detail["script"]["steps"] == script["steps"]

    assert client.delete(f"/api/scenarios/{scenario_id}").status_code == 200
    assert client.get(f"/api/scenarios/{scenario_id}").status_code == 404


def test_scenario_red_recorded(client: TestClient, spec: dict) -> None:
    """断言失败：报告 ok=False，列表记红并带失败摘要。"""
    assert client.put("/api/definitions/expense", json=spec).status_code == 200
    script = {
        "start_user": "u_zhang",
        "variables": {"amount": 100},
        "steps": [],
        "assertions": [{"type": "final_state", "equals": "completed"}],
    }
    saved = client.post(
        "/api/definitions/expense/scenarios", json={"name": "未推进", "script": script}
    ).json()
    report = client.post(f"/api/scenarios/{saved['id']}/run", json={}).json()
    assert report["ok"] is False
    listing = client.get("/api/definitions/expense/scenarios").json()
    assert listing[0]["last_ok"] is False
    assert "失败" in listing[0]["last_summary"]


def test_scenario_save_rejects_bad_script(client: TestClient) -> None:
    resp = client.post(
        "/api/definitions/expense/scenarios",
        json={"name": "坏", "script": {"steps": [{"action": "complete"}]}},
    )
    assert resp.status_code == 422


def test_inline_scenario_run_with_spec(client: TestClient, spec: dict) -> None:
    """未保存脚本直跑（草稿直通，不落库）：CI/临时验证口。"""
    resp = client.post(
        "/api/definitions/expense/scenarios/run",
        json={
            "spec": spec,
            "script": {
                "start_user": "u_zhang",
                "variables": {"amount": 100},
                "steps": [
                    {"node": "submit", "by": "u_zhang", "action": "complete"},
                    {"node": "manager", "by": "u_manager", "action": "complete"},
                ],
                "assertions": [{"type": "final_state", "equals": "completed"}],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_scenario_compare_draft_vs_published(client: TestClient, spec: dict) -> None:
    """两版对比：草稿与最新发布版同脚本并排（草稿改阈值→结论不同）。"""
    # 发布原版
    assert client.put("/api/definitions/expense", json=spec).status_code == 200
    assert client.post("/api/definitions/expense/publish").status_code == 200
    script = {
        "start_user": "u_zhang",
        "variables": {"amount": 100},
        "steps": [
            {"node": "submit", "by": "u_zhang", "action": "complete"},
            {"node": "manager", "by": "u_manager", "action": "complete"},
        ],
        "assertions": [{"type": "final_state", "equals": "completed"}],
    }
    saved = client.post(
        "/api/definitions/expense/scenarios", json={"name": "对照", "script": script}
    ).json()
    resp = client.post(f"/api/scenarios/{saved['id']}/compare", json={"spec": spec})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["published_version"].startswith("v")
    assert body["draft"]["ok"] is True
    assert body["published"]["ok"] is True
    assert body["draft"]["final_state"] == body["published"]["final_state"] == "completed"


def test_scenario_compare_runs_per_side_failure(client: TestClient, spec: dict) -> None:
    """一侧跑不起来：该侧返回失败占位，另一侧照常出结论（不整体 500）。"""
    assert client.put("/api/definitions/expense", json=spec).status_code == 200
    script = {
        "start_user": "u_zhang",
        "variables": {"amount": 100},
        "steps": [{"node": "nope", "action": "complete"}],
        "assertions": [{"type": "final_state", "equals": "completed"}],
    }
    saved = client.post(
        "/api/definitions/expense/scenarios", json={"name": "坏步", "script": script}
    ).json()
    body = client.post(f"/api/scenarios/{saved['id']}/compare", json={"spec": spec}).json()
    assert body["draft"]["ok"] is False
    assert body["draft"]["assertions"][0]["type"] == "final_state"
