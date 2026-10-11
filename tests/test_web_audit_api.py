"""审计数据面 Web API 测试（设计器插件计划批 A）。

覆盖：整图模拟（草稿直通主场景 + 发布版对照 + 校验门槛）/ 实例只读面 /
版本面 / publish 版本判定全貌。隔离语义（模拟实例不进实例列表）在此钉住——
批 B 前端 DoD 的同款断言。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flowork.contrib.web.app import create_app

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(db_file=tmp_path / "audit.db", seed=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def spec02() -> dict:
    return json.loads((EXAMPLES / "02_countersign_branch.json").read_text(encoding="utf-8"))


def _publish(client: TestClient, code: str, spec: dict) -> None:
    assert client.put(f"/api/definitions/{code}", json=spec).status_code == 200
    assert client.post(f"/api/definitions/{code}/publish").status_code == 200


def _start_and_walk(client: TestClient, code: str, spec: dict, variables: dict) -> str:
    """发布后真实启动并办结全部任务，返回实例 id（供只读面走查）。"""
    _publish(client, code, spec)
    engine = client.app.state.engine
    instance_id = engine.runtime.start_process(code, start_user="u_starter", variables=variables)
    for _ in range(10):
        tasks = engine.task.list_tasks(instance_id=instance_id)
        if not tasks:
            break
        engine.task.complete(tasks[0].id, actor=tasks[0].assignee or "u_x")
    return instance_id


# —— A1 整图模拟 ——


def _spec02_variables(spec02: dict) -> dict[str, Any]:
    """examples/02 的样例变量（按声明提取：条件引用 amount）。"""
    variables: dict[str, Any] = {}
    for var in spec02.get("variables", []):
        variables[var["name"]] = 1 if var.get("type") == "number" else "示例"
    return variables


def _simulate_body(spec02: dict, **extra: Any) -> dict[str, Any]:
    return {"spec": spec02, "variables": _spec02_variables(spec02), **extra}


def test_simulate_draft_spec_direct(client: TestClient, spec02: dict) -> None:
    """草稿模拟主场景：不发布，body 携带 spec 即出报告。"""
    resp = client.post("/api/definitions/demo/simulate", json=_simulate_body(spec02))
    assert resp.status_code == 200
    report = resp.json()
    assert report["definition_code"] == "demo"
    assert report["final_state"] in {"completed", "running"}
    assert report["visited"] and report["instance_id"]
    # 证据先于发布：code 仍无已发布版本
    assert client.get("/api/definitions/demo/versions").status_code == 404


def test_simulate_uses_saved_draft_when_no_spec(client: TestClient, spec02: dict) -> None:
    """body 不带 spec 时取已保存草稿；无草稿无发布版 → 404。"""
    assert client.post("/api/definitions/none/simulate", json={}).status_code == 404
    client.put("/api/definitions/demo", json=spec02)
    resp = client.post("/api/definitions/demo/simulate", json={"variables": {"amount": 1}})
    assert resp.status_code == 200
    assert resp.json()["definition_code"] == "demo"


def test_simulate_draft_validation_gate(client: TestClient) -> None:
    """模拟前强制设计期校验：ERROR 拦 422（与发布同形状 issues）。"""
    broken = {
        "code": "broken",
        "name": "坏定义",
        "nodes": [{"code": "n1", "kind": "user_task", "assignee": "'u'"}],
        "flows": [{"source": "n1", "target": "不存在的节点"}],
    }
    resp = client.post("/api/definitions/broken/simulate", json={"spec": broken})
    assert resp.status_code == 422
    assert resp.json()["detail"]["issues"]


def test_simulate_published_version(client: TestClient, spec02: dict) -> None:
    """?major= 模拟已发布版本（对照线上行为）。"""
    _publish(client, "demo", spec02)
    resp = client.post("/api/definitions/demo/simulate?major=1", json={"variables": {"amount": 1}})
    assert resp.status_code == 200
    assert resp.json()["definition_code"] == "demo"


def test_simulate_isolated_from_instance_list(client: TestClient, spec02: dict) -> None:
    """模拟实例不进实例列表（隔离语义钉子）；include_simulated=true 可见。"""
    client.post("/api/definitions/demo/simulate", json=_simulate_body(spec02))
    assert client.get("/api/instances").json() == []
    included = client.get("/api/instances?include_simulated=true").json()
    assert [i["definition_code"] for i in included] == ["demo"]


# —— A2 实例只读面 ——


def test_instance_read_surface(client: TestClient, spec02: dict) -> None:
    instance_id = _start_and_walk(client, "demo", spec02, _spec02_variables(spec02))
    detail = client.get(f"/api/instances/{instance_id}").json()
    assert detail["instance"]["id"] == instance_id
    assert detail["instance"]["definition_code"] == "demo"
    assert isinstance(detail["tokens"], list) and detail["tokens"]
    assert isinstance(detail["variables"], dict)
    # 时间线：令牌 + 已办按时间合并
    timeline = client.get(f"/api/instances/{instance_id}/timeline").json()
    kinds = {entry["kind"] for entry in timeline}
    assert {"token", "task"} <= kinds
    # 路由决策：每行有 source_activity 与 selected
    decisions = client.get(f"/api/instances/{instance_id}/route-decisions").json()
    assert all("selected" in row and "source_activity" in row for row in decisions)
    # 已办任务（分页信封）
    tasks = client.get(f"/api/instances/{instance_id}/tasks").json()
    assert tasks["total"] >= 1 and tasks["items"]
    # 变量历史：birth 行（发起变量记 old=None）
    history = client.get(f"/api/instances/{instance_id}/variable-history").json()
    assert any(row["old_value"] is None for row in history)
    by_name = client.get(f"/api/instances/{instance_id}/variable-history?name=不存在的键").json()
    assert by_name == []
    # 查无实例 → 404
    assert client.get("/api/instances/nope").status_code == 404


def test_instance_list_filters(client: TestClient, spec02: dict) -> None:
    _start_and_walk(client, "demo", spec02, _spec02_variables(spec02))
    body = client.get("/api/instances?definition_code=demo").json()
    assert [i["definition_code"] for i in body] == ["demo"]
    running = client.get("/api/instances?state=running").json()
    assert all(i["state"] == "running" for i in running)
    other = client.get("/api/instances?definition_code=absent").json()
    assert other == []


# —— A3 publish 版本判定全貌 ——


def test_publish_returns_full_verdict(client: TestClient, spec02: dict) -> None:
    resp = client.post("/api/definitions/demo/publish__missing")
    assert resp.status_code == 404
    client.put("/api/definitions/demo", json=spec02)
    verdict = client.post("/api/definitions/demo/publish").json()
    assert verdict["status"] == "published"
    assert verdict["triggered_rules"] == []  # 首次发布无破坏性
    assert verdict["identical_to"] is None
    # 内容不变再发：幂等去重——identical_to 指向已发布版本、不落新版本
    client.put("/api/definitions/demo", json=spec02)
    again = client.post("/api/definitions/demo/publish").json()
    assert again["identical_to"] == [verdict["version"], verdict["revision"]]


# —— A4 版本面 ——


def test_versions_surface(client: TestClient, spec02: dict) -> None:
    _publish(client, "demo", spec02)
    versions = client.get("/api/definitions/demo/versions").json()
    assert [v["major"] for v in versions] == [1]
    assert versions[0]["published_at"]
    spec = client.get("/api/definitions/demo/versions/1").json()
    assert spec["code"] == "demo"
    assert client.get("/api/definitions/demo/versions/99").status_code == 404
    assert client.get("/api/definitions/absent/versions").status_code == 404
