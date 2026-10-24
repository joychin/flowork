"""REST /v2 行为测试：部署→发起→办理→变量→signal→jobs 全链（openface-plugin-design.md §7）。"""

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


def _simple_spec() -> dict[str, Any]:
    definition = (
        FlowBuilder("rest_simple", "REST 简单审批")
        .start()
        .user_task("manager", name="经理审批", assignee="'u_manager'")
        .end()
        .connect("start", "manager")
        .connect("manager", "end")
        .build(variables=[ProcessVariable(name="amount", type="number")])
    )
    return dump_definition(definition)


def _wait_spec() -> dict[str, Any]:
    definition = (
        FlowBuilder("rest_wait", "REST 等回函")
        .start()
        .wait("w", message={"name": "sign_back"})
        .end()
        .connect("start", "w")
        .connect("w", "end")
        .build()
    )
    return dump_definition(definition)


def _deploy(client: TestClient, spec: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v2/definitions/deploy", json={"spec": spec})
    assert response.status_code == 200, response.text
    return response.json()


def _start(client: TestClient, code: str, **kwargs: Any) -> str:
    response = client.post(
        "/v2/instances", json={"code": code, "start_user": "u_requester", **kwargs}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["instance_id"])


def test_deploy_search_and_get(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    verdict = _deploy(client, _simple_spec())
    assert verdict["code"] == "rest_simple" and verdict["next_major"] == 1

    rows = client.post("/v2/definitions/search", json={}).json()["items"]
    assert any(row["code"] == "rest_simple" for row in rows)

    fetched = client.get("/v2/definitions/rest_simple").json()
    assert fetched["code"] == "rest_simple"
    assert any(node["code"] == "manager" for node in fetched["nodes"])


def test_deploy_invalid_spec_422_with_issues(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    spec = _simple_spec()
    spec["nodes"][1]["assignee"] = "role:legal"  # 人员位组引用＝设计期 ERROR（§5.5）
    response = client.post("/v2/definitions/deploy", json={"spec": spec})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "InvalidDefinition"
    assert body["error"]["details"]["issues"]


def test_start_search_complete_flow(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    _deploy(client, _simple_spec())
    instance_id = _start(client, "rest_simple", variables={"amount": 100})

    rows = client.post("/v2/tasks/search", json={"filter": {"assignee": "u_manager"}}).json()[
        "items"
    ]
    assert len(rows) == 1 and rows[0]["instance_id"] == instance_id

    result = client.post(
        f"/v2/tasks/{rows[0]['id']}/complete",
        json={"comment": "同意", "variables": {"amount": 200}},
    ).json()
    assert result["instance_state"] == "completed"

    items = client.post("/v2/instances/search", json={"filter": {"state": "completed"}}).json()[
        "items"
    ]
    assert any(item["id"] == instance_id for item in items)


def test_start_idempotency_conflict_409(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    _deploy(client, _simple_spec())
    _start(client, "rest_simple", idempotency_key="biz-1")
    response = client.post(
        "/v2/instances",
        json={"code": "rest_simple", "start_user": "u_requester", "idempotency_key": "biz-1"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DuplicateStartError"


def test_variables_surface(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    _deploy(client, _simple_spec())
    instance_id = _start(client, "rest_simple", variables={"amount": 100})

    assert client.get(f"/v2/instances/{instance_id}/variables").json()["amount"] == 100

    patched = client.patch(
        f"/v2/instances/{instance_id}/variables",
        json={"updates": {"amount": 300}, "actor": "u_ops"},
    )
    assert patched.status_code == 200

    history = client.get(f"/v2/instances/{instance_id}/variables/history").json()["items"]
    assert any(
        row["name"] == "amount" and row["new_value"] == 300 and row["acted_by"] == "u_ops"
        for row in history
    )


def test_signal_wait_instance(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    _deploy(client, _wait_spec())
    instance_id = _start(client, "rest_wait")

    receipt = client.post(
        f"/v2/instances/{instance_id}/signal",
        json={"name": "sign_back", "payload": {"signed": True}},
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["instance_id"] == instance_id

    items = client.post("/v2/instances/search", json={"filter": {"state": "completed"}}).json()[
        "items"
    ]
    assert any(item["id"] == instance_id for item in items)


def test_signal_not_waiting_404(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    _deploy(client, _simple_spec())
    instance_id = _start(client, "rest_simple")
    response = client.post(f"/v2/instances/{instance_id}/signal", json={"name": "sign_back"})
    assert response.status_code == 404


def test_claim_via_candidate(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    definition = (
        FlowBuilder("rest_claim", "REST 认领")
        .start()
        .user_task("review", candidate_users=["'u_bob'"])
        .end()
        .connect("start", "review")
        .connect("review", "end")
        .build()
    )
    _deploy(client, dump_definition(definition))
    instance_id = _start(client, "rest_claim")

    rows = client.post("/v2/tasks/search", json={"filter": {"assignee": ""}}).json()["items"]
    assert [row["instance_id"] for row in rows] == [instance_id]

    claimed = client.post(f"/v2/tasks/{rows[0]['id']}/claim", json={"user": "u_bob"})
    assert claimed.status_code == 200 and claimed.json()["assignee"] == "u_bob"


def test_anticipate(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    _deploy(client, _simple_spec())
    _start(client, "rest_simple")
    task_id = client.post("/v2/tasks/search", json={"filter": {"assignee": "u_manager"}}).json()[
        "items"
    ][0]["id"]
    result = client.post(f"/v2/tasks/{task_id}/anticipate", json={"outcome": "agree"}).json()
    assert result["node_exit"] is True and result["instance_complete"] is True


def test_jobs_activate_complete_and_search(env: tuple[TestClient, ProcessEngine]) -> None:
    client, engine = env
    _deploy(client, _simple_spec())
    instance_id = _start(client, "rest_simple")
    engine.worker_jobs.create_worker_job(job_type="notify_email", instance_id=instance_id)

    claims = client.post(
        "/v2/jobs/activate",
        json={"job_types": ["notify_email"], "worker_id": "w1", "request_timeout": 0},
    ).json()["jobs"]
    assert len(claims) == 1

    done = client.post(
        f"/v2/jobs/{claims[0]['job_id']}/complete", json={"worker_id": "w1", "output": {"ok": True}}
    )
    assert done.status_code == 200

    rows = client.post("/v2/jobs/search", json={"filter": {"state": "done"}}).json()["items"]
    assert [row["id"] for row in rows] == [claims[0]["job_id"]]


def test_events_and_incidents_search(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    _deploy(client, _simple_spec())
    instance_id = _start(client, "rest_simple")

    events = client.post("/v2/events/search", json={"filter": {"instance_id": instance_id}}).json()
    assert events["items"] and events["next_cursor"] is None
    assert client.post("/v2/incidents/search", json={}).json()["items"] == []


def test_error_body_shape(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    response = client.get("/v2/definitions/unknown_code")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "DefinitionNotPublishedError"
    assert "message" in body["error"] and "details" in body["error"]


def test_cursor_pagination(env: tuple[TestClient, ProcessEngine]) -> None:
    client, _ = env
    for index in range(3):
        spec = _simple_spec()
        spec["code"] = f"rest_page_{index}"
        _deploy(client, spec)

    page = client.post(
        "/v2/definitions/search",
        json={"filter": {"code_prefix": "rest_page_"}, "page": {"limit": 2}},
    ).json()
    assert len(page["items"]) == 2 and page["next_cursor"]

    rest = client.post(
        "/v2/definitions/search",
        json={
            "filter": {"code_prefix": "rest_page_"},
            "page": {"after": page["next_cursor"], "limit": 2},
        },
    ).json()
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None


def test_messages_search_covers_buffer_and_subscription(
    env: tuple[TestClient, ProcessEngine],
) -> None:
    """收尾批 6（spec §12 搜索族补齐）：订阅 open→matched 全程 + 缓冲两源检索。"""
    client, _ = env
    _deploy(client, _wait_spec())
    instance_id = _start(client, "rest_wait")  # wait 节点进入即登记 open 订阅
    open_rows = client.post(
        "/v2/messages/search", json={"filter": {"kind": "subscription", "state": "open"}}
    ).json()["items"]
    assert len(open_rows) == 1 and open_rows[0]["instance_id"] == instance_id

    client.post("/v2/messages/publish", json={"name": "sign_back"})  # 命中订阅 → matched
    client.post(
        "/v2/messages/publish",
        json={"name": "evt.buffered", "correlation_key": "corr-42", "payload": {"x": 1}},
    )
    body = client.post("/v2/messages/search", json={"filter": {}}).json()
    kinds = {row["kind"] for row in body["items"]}
    assert kinds == {"buffer", "subscription"}

    only_buffer = client.post("/v2/messages/search", json={"filter": {"kind": "buffer"}}).json()[
        "items"
    ]
    assert [row["name"] for row in only_buffer] == ["evt.buffered"]

    by_prefix = client.post(
        "/v2/messages/search", json={"filter": {"correlation_key": "corr-"}}
    ).json()["items"]
    assert [row["correlation_key"] for row in by_prefix] == ["corr-42"]

    matched = client.post(
        "/v2/messages/search",
        json={"filter": {"kind": "subscription", "state": "matched"}},
    ).json()["items"]
    assert matched and matched[0]["state"] == "matched"


# —— 2026-10-24 取人对打修复批回归：三面同构欠账 ——
# 来源：subagent 对打 FINDINGS（tests 之外证据在 out/persona/FINDINGS.md）——
# 已办查询死档（state=completed 恒空）、无实例详情、REST 缺 consult 族动作。


def test_tasks_history_search_returns_archived_rows(env: tuple[TestClient, ProcessEngine]) -> None:
    """已办查询：终态行从待办表删除入 wf_task_history，「我办过的单」从这里查。"""
    client, _ = env
    _deploy(client, _simple_spec())
    iid = _start(client, "rest_simple")
    task_id = client.post("/v2/tasks/search", json={"filter": {"instance_id": iid}}).json()[
        "items"
    ][0]["id"]
    client.post(f"/v2/tasks/{task_id}/complete", json={"actor": "u_manager"})

    # 待办面：办结行消失（终态行即删）；state=completed 已收窄为 422 指路历史端点
    ready = client.post("/v2/tasks/search", json={"filter": {"instance_id": iid}}).json()
    assert ready["items"] == []
    legacy = client.post("/v2/tasks/search", json={"filter": {"state": "completed"}})
    assert legacy.status_code == 422
    assert "history" in legacy.json()["error"]["message"]

    history = client.post(
        "/v2/tasks/history/search", json={"filter": {"assignee": "u_manager"}}
    ).json()
    assert [row["activity_code"] for row in history["items"]] == ["manager"]
    assert history["items"][0]["outcome"] == "agree"


def test_instance_detail_endpoint(env: tuple[TestClient, ProcessEngine]) -> None:
    """GET /v2/instances/{id}：详情端点（此前 404，fin2 对打撞上）。"""
    client, _ = env
    _deploy(client, _simple_spec())
    iid = _start(client, "rest_simple")
    detail = client.get(f"/v2/instances/{iid}")
    assert detail.status_code == 200
    assert detail.json()["id"] == iid
    assert client.get("/v2/instances/none-such").status_code == 404


def test_consult_actions_roundtrip(env: tuple[TestClient, ProcessEngine]) -> None:
    """consult/complete_consult/add_countersigner/withdraw 四动作（三面同构补齐）。"""
    client, _ = env
    spec = _simple_spec()
    spec["nodes"][1]["assignees"] = ["'u_manager'"]
    _deploy(client, spec)
    iid = _start(client, "rest_simple")
    task_id = client.post("/v2/tasks/search", json={"filter": {"instance_id": iid}}).json()[
        "items"
    ][0]["id"]

    created = client.post(
        f"/v2/tasks/{task_id}/consult",
        json={"actor": "u_manager", "users": ["u_advisor"], "comment": "问一下"},
    )
    assert created.status_code == 200
    opinion_id = created.json()[0]["id"]

    done = client.post(
        f"/v2/tasks/{opinion_id}/complete_consult",
        json={"actor": "u_advisor", "opinion": "agree"},
    )
    assert done.status_code == 200 and done.json()["completed"] is True

    added = client.post(
        f"/v2/tasks/{task_id}/add_countersigner",
        json={"actor": "u_manager", "users": ["u_extra"]},
    )
    assert added.status_code == 200

    # withdraw 作用于已办——只 complete 主任务（u_extra 的票未投，实例仍在运行态，
    # 终态实例拒绝撤回是既有语义），随后 u_manager 撤回自己的已办
    client.post(f"/v2/tasks/{task_id}/complete", json={"actor": "u_manager"})
    withdrawn = client.post(
        f"/v2/tasks/{task_id}/withdraw", json={"actor": "u_manager", "comment": "改主意"}
    )
    assert withdrawn.status_code == 200, withdrawn.text
