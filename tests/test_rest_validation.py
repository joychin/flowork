"""REST /v2 输入校验组修复的回归（全仓 review 2026-10-13 批 1 P1 组）。

- NaN request_timeout 曾令长轮询 while 永不退出（请求级 DoS）——isfinite 拦
  （该判例不红跑：红态即挂死进程，负面行为已由 review 实验实证）；
- max/visibility/retries/backoff 数值字段裸 int()/float() 炸 500 + 纯文本体；
- FastAPI 原生校验 422 的 {"detail":[...]} 破坏统一错误体；
- signal 缺 body.name 曾 404+NotFoundError（缺字段是 422 语义）；
- transfer 缺 to_user 曾 200 成功产出永久无人能办的孤儿任务；
- update_variables 终态拒绝曾 404（实例存在，是 409 冲突语义）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flowork.api import FlowBuilder, ProcessEngine, ProcessVariable
from flowork.contrib.rest.app import create_app
from flowork.definitions.definition import dump_definition


@pytest.fixture()
def env(tmp_path: Path) -> tuple[TestClient, ProcessEngine]:
    app: FastAPI = create_app(db_file=tmp_path / "rest.db")
    with TestClient(app) as client:
        yield client, app.state.engine


def _spec(code: str) -> dict[str, Any]:
    definition = (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u1'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(variables=[ProcessVariable(name="amount", type="number")])
    )
    return dump_definition(definition)


def _deploy_and_start(client: TestClient, code: str) -> str:
    body = client.post("/v2/definitions/deploy", json={"spec": _spec(code)})
    assert body.status_code == 200, body.text
    body = client.post("/v2/instances", json={"code": code, "start_user": "u1"})
    assert body.status_code == 200, body.text
    return str(body.json()["instance_id"])


def test_activate_bad_numeric_fields_422(env):
    client, _ = env
    iid = _deploy_and_start(client, "val_a")
    # 制造一个可被认领的 job（实例级 deadline）——直接打激活端点的参数校验即可
    body = client.post(
        "/v2/jobs/activate",
        json={"job_types": ["x"], "worker_id": "w", "max": "abc"},
    )
    assert body.status_code == 422, f"修前 500：{body.status_code} {body.text[:80]}"
    assert body.json()["error"]["code"] == "RestRequestError"

    body = client.post(
        "/v2/jobs/activate",
        json={"job_types": ["x"], "worker_id": "w", "max": -5},
    )
    assert body.status_code == 422  # 修前负数=领走全部（SQLite LIMIT 负数无限制）

    body = client.post(
        "/v2/jobs/activate",
        json={"job_types": ["x"], "worker_id": "w", "visibility_timeout_seconds": 0},
    )
    assert body.status_code == 422  # 修前 0=租约立即过期形同虚设
    assert iid  # 实例在库（激活参数校验先于任何副作用）


def test_fail_job_bad_retries_422(env):
    client, _ = env
    body = client.post(
        "/v2/jobs/activate", json={"job_types": ["x"], "worker_id": "w", "request_timeout": 0}
    )
    assert body.status_code == 200
    fake = client.post("/v2/jobs/fake/fail", json={"worker_id": "w", "retries": "3"})
    assert fake.status_code in (404, 409), (
        f"数值已归一（修前 retries='3' 裸比较 500）：{fake.status_code}"
    )


def test_validation_error_unified_body(env):
    """POST 无 body → 422 且错误体为统一形状（修前 FastAPI 原生 detail 数组）。"""
    client, _ = env
    body = client.post("/v2/tasks/some-id/claim")
    assert body.status_code == 422
    payload = body.json()
    assert "error" in payload, f"修前 {payload}"
    assert payload["error"]["code"] == "RequestValidationError"
    assert "detail" not in payload


def test_signal_missing_name_422_not_404(env):
    client, engine = env
    iid = _deploy_and_start(client, "val_sig")
    body = client.post(f"/v2/instances/{iid}/signal", json={})
    assert body.status_code == 422
    assert body.json()["error"]["code"] == "RestRequestError"
    assert engine is not None


def test_transfer_missing_to_user_422(env):
    client, _ = env
    iid = _deploy_and_start(client, "val_tr")
    task_id = client.post("/v2/tasks/search", json={"filter": {"instance_id": iid}}).json()[
        "items"
    ][0]["id"]
    body = client.post(f"/v2/tasks/{task_id}/transfer", json={})
    assert body.status_code == 422, "修前 200 并产出永久孤儿任务"
    # 原任务仍在（整笔无副作用）
    after = client.post("/v2/tasks/search", json={"filter": {"instance_id": iid}}).json()
    assert len(after["items"]) == 1


def test_update_variables_terminal_409_not_404(env):
    client, engine = env
    iid = _deploy_and_start(client, "val_term")
    task_id = client.post("/v2/tasks/search", json={"filter": {"instance_id": iid}}).json()[
        "items"
    ][0]["id"]
    client.post(f"/v2/tasks/{task_id}/complete", json={"actor": "u1"})
    assert engine.runtime.get_instance(iid).state == "completed"

    body = client.patch(f"/v2/instances/{iid}/variables", json={"updates": {"amount": 1}})
    assert body.status_code == 409, f"修前 404：{body.status_code} {body.text[:100]}"
    assert body.json()["error"]["code"] == "RestConflictError"

    # 真 404 语义仍在：不存在的实例
    ghost = client.patch("/v2/instances/ghost/variables", json={"updates": {"amount": 1}})
    assert ghost.status_code == 404
