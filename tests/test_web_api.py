"""Web API 测试：定义 CRUD / 校验 / 发布 / 节点类型元数据。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowork.web.app import create_app

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(db_file=tmp_path / "api.db", seed=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def spec02() -> dict:
    return json.loads((EXAMPLES / "02_countersign_branch.json").read_text(encoding="utf-8"))


def test_node_kinds_builtin(client: TestClient) -> None:
    kinds = {item["kind"]: item for item in client.get("/api/node-kinds").json()}
    assert set(kinds) >= {"start", "end", "branch", "user_task", "service_task", "cc_task"}
    # 调色板两个分支变体：命中即走 / 命中的都走
    palette = kinds["branch"]["ui"]["palette"]
    assert [item["defaults"]["route_mode"] for item in palette] == [
        "first_match", "all_match",
    ]
    # schema 驱动表单：user_task 的 schema 含 multi_instance（$defs 解析）
    schema = kinds["user_task"]["schema"]
    assert "MultiInstance" in schema.get("$defs", {})
    assert "assignee" in schema["properties"]


def test_save_list_get_draft(client: TestClient, spec02: dict) -> None:
    saved = client.put("/api/definitions/countersign_branch", json=spec02)
    assert saved.status_code == 200
    assert saved.json()["version"] == 1

    # 再次保存：就地更新草稿，版本不膨胀
    spec02["name"] = "改名了"
    again = client.put("/api/definitions/countersign_branch", json=spec02)
    assert again.json()["version"] == 1

    listing = client.get("/api/definitions").json()
    assert listing == [
        {"code": "countersign_branch", "name": "改名了", "draft_version": 1,
         "published_version": None, "updated_at": listing[0]["updated_at"]},
    ]

    got = client.get("/api/definitions/countersign_branch").json()
    assert got["draft_version"] == 1
    assert got["spec"]["name"] == "改名了"
    assert len(got["spec"]["nodes"]) == 6


def test_validate_reports_issue_with_location(client: TestClient) -> None:
    broken = {
        "code": "broken", "name": "坏定义", "nodes": [
            {"code": "start", "name": "发起", "kind": "start"},
            {"code": "task", "name": "审批", "kind": "user_task"},
        ],
        "flows": [{"source": "start", "target": "task"}],
    }
    result = client.post("/api/validate", json=broken)
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is False
    deadend = next(issue for issue in body["issues"] if issue["rule"] == "structure.node.deadend")
    assert deadend["node"] == "task"


def test_publish_blocked_by_error_then_ok(client: TestClient) -> None:
    broken = {
        "code": "broken", "name": "坏定义", "nodes": [
            {"code": "start", "name": "发起", "kind": "start"},
            {"code": "task", "name": "审批", "kind": "user_task"},
        ],
        "flows": [{"source": "start", "target": "task"}],
    }
    client.put("/api/definitions/broken", json=broken)
    blocked = client.post("/api/definitions/broken/publish")
    assert blocked.status_code == 422  # task 无出边 → deadend ERROR，禁止发布
    detail = blocked.json()["detail"]
    assert detail["message"] == "校验未通过，禁止发布"
    assert any(issue["rule"] == "structure.node.deadend" for issue in detail["issues"])


def test_publish_valid_definition(client: TestClient, spec02: dict) -> None:
    client.put("/api/definitions/countersign_branch", json=spec02)
    published = client.post("/api/definitions/countersign_branch/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    got = client.get("/api/definitions/countersign_branch").json()
    assert got["published_version"] == 1
    assert got["draft_version"] is None  # 草稿已随发布消费
    # 再次编辑 → 新草稿版本 v2
    spec02["name"] = "第二稿"
    saved = client.put("/api/definitions/countersign_branch", json=spec02)
    assert saved.json()["version"] == 2
    got2 = client.get("/api/definitions/countersign_branch").json()
    assert got2["draft_version"] == 2
    assert got2["published_version"] == 1


def test_unknown_kind_rejected(client: TestClient) -> None:
    bad = {"code": "x", "nodes": [{"code": "n1", "kind": "nope"}], "flows": []}
    resp = client.post("/api/validate", json=bad)
    assert resp.status_code == 422
    assert "nope" in resp.json()["detail"]


def test_seed_examples(tmp_path: Path) -> None:
    app = create_app(db_file=tmp_path / "seed.db", seed=True)
    with TestClient(app) as client:
        listing = client.get("/api/definitions").json()
        by_code = {item["code"]: item for item in listing}
        assert {"simple_approval", "countersign_branch", "call_activity_demo"} <= set(by_code)
        assert by_code["simple_approval"]["published_version"] == 1  # 首个示例自动发布
        assert by_code["countersign_branch"]["draft_version"] == 1
