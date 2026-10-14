"""REST /v2 分页游标与错误映射修复的回归（全仓 review 2026-10-13 批 1）。

- P0-1：instances/events 搜索取数窗口原恰为 offset+limit（无 +1 余量探测），
  next_cursor 恒 None——第二页数据不可达（对照 jobs/messages 四端点正确，
  既有测试恰好只测了 definitions 游标）；
- P0-3：InputContractError 不在错误映射表，变量写入校验拒绝全 500；
- P1-4：巨 offset（解码 >2^63）直推 SQL 炸 sqlite 绑定（裸 500）。
"""

from __future__ import annotations

import base64
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
        .user_task("a", assignee="'u1'", multi_instance=None)
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(variables=[ProcessVariable(name="amount", type="number")])
    )
    return dump_definition(definition)


def _cursor_of(body: dict[str, Any]) -> str | None:
    return body["next_cursor"]


def _after(offset: int) -> dict[str, Any]:
    return {"page": {"after": base64.b64encode(f"o:{offset}".encode()).decode("ascii")}}


def test_instances_search_walks_all_pages(env):
    client, _engine = env
    client.post("/v2/definitions/deploy", json={"spec": _spec("pg_inst")})
    for _ in range(5):
        client.post("/v2/instances", json={"code": "pg_inst", "start_user": "u1"})

    seen: list[str] = []
    after: dict[str, Any] = {}
    for _ in range(10):
        body = client.post("/v2/instances/search", json={"page": {"limit": 2}, **after})
        assert body.status_code == 200
        payload = body.json()
        seen.extend(item["id"] for item in payload["items"])
        cursor = _cursor_of(payload)
        if cursor is None:
            break
        after = _after(int(base64.b64decode(cursor).decode("ascii")[2:]))
    assert len(seen) == 5, "五实例必须经标准翻页全部可达（修前第二页起静默丢失）"
    assert len(set(seen)) == 5


def test_events_search_walks_all_pages(env):
    client, engine = env
    client.post("/v2/definitions/deploy", json={"spec": _spec("pg_evt")})
    for _ in range(3):
        client.post("/v2/instances", json={"code": "pg_evt", "start_user": "u1"})

    seen: list[str] = []
    after: dict[str, Any] = {}
    for _ in range(20):
        body = client.post("/v2/events/search", json={"page": {"limit": 2}, **after})
        assert body.status_code == 200
        payload = body.json()
        seen.extend(item["id"] for item in payload["items"])
        cursor = _cursor_of(payload)
        if cursor is None:
            break
        after = _after(int(base64.b64decode(cursor).decode("ascii")[2:]))
    total = engine.history.events(limit=1000)
    assert len(seen) == len(total), "事件翻页必须与全量等长（修前恒 None 截断）"


def test_huge_offset_cursor_rejected_not_500(env):
    client, _engine = env
    huge = base64.b64encode(f"o:{10**28}".encode()).decode("ascii")
    body = client.post("/v2/messages/search", json={"page": {"after": huge}})
    assert body.status_code == 422
    assert body.json()["error"]["code"] == "RestRequestError"


def test_input_contract_error_maps_to_422(env):
    """变量写入校验拒绝（未声明键）＝422（修前 InputContractError 漏映射全 500）。"""
    client, _engine = env
    client.post("/v2/definitions/deploy", json={"spec": _spec("pg_var")})
    iid = client.post("/v2/instances", json={"code": "pg_var", "start_user": "u1"}).json()[
        "instance_id"
    ]
    body = client.patch(
        f"/v2/instances/{iid}/variables",
        json={"updates": {"undeclared_key": 1}},
    )
    assert body.status_code == 422, f"修前此处 500：{body.status_code} {body.text[:120]}"
    assert body.json()["error"]["code"] == "InputContractError"
