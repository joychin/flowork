"""REST /v2 面快照守卫（openface-plugin-design.md §7，模式对齐 test_web_api_surface.py）。

REST /v2 是对外集成契约——宿主与外部系统按此路由表对接，升级插件不能随便断。
本测试把端点清单钉死：增删路径或方法=红灯=显式动作（同步本快照 + 设计文档 §2.1）。
"""

from __future__ import annotations

from fastapi.routing import APIRoute

import flowork.api
from flowork.contrib.rest import REQUIRES_FLOWORK_API
from flowork.contrib.rest.app import create_app

# 冻结清单（格式 "METHOD path"；spec §12 全量 + 设计 §2.1 补缺的发起端点 + health）
EXPECTED: set[str] = {
    "GET /v2/health",
    # definitions
    "POST /v2/definitions/search",
    "POST /v2/definitions/deploy",
    "GET /v2/definitions/{code}",
    "POST /v2/definitions/{code}/retire",
    # instances（POST /v2/instances = spec §12 缺口补齐：对外集成面必须有发起端点）
    "POST /v2/instances",
    "POST /v2/instances/search",
    "POST /v2/instances/{instance_id}/pause",
    "POST /v2/instances/{instance_id}/resume",
    "POST /v2/instances/{instance_id}/cancel",
    "POST /v2/instances/{instance_id}/terminate",
    "POST /v2/instances/{instance_id}/signal",
    "GET /v2/instances/{instance_id}/variables",
    "PATCH /v2/instances/{instance_id}/variables",
    "GET /v2/instances/{instance_id}/variables/history",
    # tasks
    "POST /v2/tasks/search",
    "POST /v2/tasks/{task_id}/claim",
    "POST /v2/tasks/{task_id}/unclaim",
    "POST /v2/tasks/{task_id}/complete",
    "POST /v2/tasks/{task_id}/reject",
    "POST /v2/tasks/{task_id}/transfer",
    "POST /v2/tasks/{task_id}/assist",
    "POST /v2/tasks/{task_id}/complete_assist",
    "POST /v2/tasks/{task_id}/anticipate",
    # system
    "POST /v2/messages/publish",
    "POST /v2/jobs/activate",
    "POST /v2/jobs/{job_id}/complete",
    "POST /v2/jobs/{job_id}/fail",
    "POST /v2/jobs/search",
    "POST /v2/incidents/search",
    "POST /v2/events/search",
}


def test_rest_v2_surface_frozen(tmp_path) -> None:
    app = create_app(db_file=tmp_path / "surface.db")
    actual = {
        f"{next(iter(route.methods))} {route.path}"
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/v2")
    }
    assert actual == EXPECTED, (
        "REST /v2 面增删须显式：同步本快照 + openface-plugin-design.md §2.1。\n"
        f"新增: {sorted(actual - EXPECTED)}\n移除: {sorted(EXPECTED - actual)}"
    )


def test_rest_plugin_compat_declaration() -> None:
    """插件兼容声明 ≤ 当前门面版本（openface-plugin-design.md §4）。"""
    assert REQUIRES_FLOWORK_API <= flowork.api.API_VERSION


def test_route_table_spec_coverage() -> None:
    """spec §12 的每个端点族都有落点（动作后缀逐个核，防快照拼错漏族）。"""
    suffixes = (
        "claim",
        "unclaim",
        "complete",
        "reject",
        "transfer",
        "assist",
        "complete_assist",
        "anticipate",
    )
    for suffix in suffixes:
        assert any(path.endswith(f"/{suffix}") for path in EXPECTED), suffix
    for family in ("definitions", "instances", "tasks", "jobs", "incidents", "messages", "events"):
        assert any(f"/v2/{family}" in path for path in EXPECTED), family


def test_unexpected_cursor_before_rejected(tmp_path) -> None:
    """before 游标明示不支持（设计 §2.2，不假装游标语义）。"""
    from fastapi.testclient import TestClient

    app = create_app(db_file=tmp_path / "cursor.db")
    with TestClient(app) as client:
        response = client.post("/v2/definitions/search", json={"page": {"before": "aGk="}})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "RestRequestError"
