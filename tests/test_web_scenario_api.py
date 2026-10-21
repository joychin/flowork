"""场景 web 面：脚本存文件（与定义同目录）、读写删、直跑。

场景是可复跑的审阅资产：文件为事实源，CLI `flowork scenario run` 读同一份。
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
    definitions = tmp_path / "defs"
    definitions.mkdir()
    app = create_app(db_file=tmp_path / "reh.db", definitions_dir=definitions)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def defs_dir(tmp_path: Path) -> Path:
    return tmp_path / "defs"


@pytest.fixture()
def spec() -> dict:
    return json.loads((EXAMPLES / "04_expense_claim.json").read_text(encoding="utf-8"))


def test_scenario_file_roundtrip(client: TestClient, defs_dir: Path) -> None:
    """场景存成定义同目录的 <code>.scenario.json：写入 → 读回 → 删掉。"""
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
    # 还没有场景文件
    assert client.get("/api/definitions/expense/scenario").status_code == 404

    saved = client.put("/api/definitions/expense/scenario", json={"script": script})
    assert saved.status_code == 200, saved.text
    assert saved.json()["file"] == "expense.scenario.json"
    # 真的落到磁盘（与定义同目录，能进 git）
    on_disk = json.loads((defs_dir / "expense.scenario.json").read_text(encoding="utf-8"))
    assert on_disk["steps"] == script["steps"]

    loaded = client.get("/api/definitions/expense/scenario").json()
    assert loaded["file"] == "expense.scenario.json"
    assert loaded["script"]["name"] == "小额直通"

    assert client.delete("/api/definitions/expense/scenario").status_code == 200
    assert not (defs_dir / "expense.scenario.json").exists()
    assert client.get("/api/definitions/expense/scenario").status_code == 404


def test_scenario_file_survives_restart(tmp_path: Path, defs_dir: Path) -> None:
    """场景是文件不是会话状态：换一个进程（新 app）照样读得到。"""
    script = {"name": "跨会话", "steps": [], "assertions": []}
    first = create_app(db_file=":memory:", definitions_dir=defs_dir)
    with TestClient(first) as client:
        resp = client.put("/api/definitions/expense/scenario", json={"script": script})
        assert resp.status_code == 200
    second = create_app(db_file=":memory:", definitions_dir=defs_dir)
    with TestClient(second) as client:
        assert client.get("/api/definitions/expense/scenario").json()["script"]["name"] == "跨会话"


def test_scenario_write_surface_absent_without_dir(tmp_path: Path) -> None:
    """纯内存会话没有落盘位置：写面不注册，而不是假装能保存。"""
    app = create_app(db_file=":memory:")
    with TestClient(app) as client:
        assert client.get("/api/definitions/expense/scenario").status_code == 404
        resp = client.put("/api/definitions/expense/scenario", json={"script": {}})
        assert resp.status_code == 404


def test_scenario_save_rejects_bad_script(client: TestClient, defs_dir: Path) -> None:
    resp = client.put(
        "/api/definitions/expense/scenario",
        json={"script": {"steps": [{"action": "complete"}]}},
    )
    assert resp.status_code == 422
    assert not (defs_dir / "expense.scenario.json").exists()


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
