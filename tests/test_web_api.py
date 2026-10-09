"""Web API 测试：定义 CRUD / 校验 / 发布 / 节点类型元数据。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowork.contrib.web.app import create_app

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
        "first_match",
        "all_match",
    ]
    # schema 驱动表单：user_task 的 schema 含 multi_instance（$defs 解析）
    schema = kinds["user_task"]["schema"]
    assert "MultiInstance" in schema.get("$defs", {})
    assert "assignee" in schema["properties"]


def test_node_kinds_form_groups_cover_fields(client: TestClient) -> None:
    """设计器表单覆盖反向审计：注册表里每个 kind 的 schema 字段都必须进某个 form_groups。

    正向是 schema 驱动（未分组字段自动落到「其他」组）；本条防「落进其他组=界面上
    没名字」——deadlines/binding/multi_instance 曾全部掉出分组（2026-10-09 走查发现）。
    advance_policy 例外：设计器用专属的「开始条件」选项卡控件渲染，不走 schema 表单。
    """
    kinds = {item["kind"]: item for item in client.get("/api/node-kinds").json()}
    for kind, item in kinds.items():
        props = set(item["schema"].get("properties") or {})
        grouped = {f for g in item["ui"].get("form_groups", []) for f in g["fields"]}
        leftover = props - grouped - set(item["form_exclude"]) - {"advance_policy"}
        assert not leftover, f"节点 {kind} 有字段未入任何表单分组：{sorted(leftover)}"


def test_node_kinds_designer_metadata_for_wait_and_call(client: TestClient) -> None:
    """等待节点/子流程的设计器元数据：路径标签 + 调色板形态 + 图标（走查补齐批）。"""
    kinds = {item["kind"]: item for item in client.get("/api/node-kinds").json()}
    wait_ui = kinds["wait"]["ui"]
    assert wait_ui["icon"] == "clock"
    assert [p["label"] for p in wait_ui["palette"]] == [
        "等待消息",
        "延时等待",
        "等消息+超时(竞速)",
    ]
    labels = wait_ui["field_labels"]
    assert labels["message.name"] == "消息名(发布同名消息即唤醒)"
    assert labels["timeout.after_minutes"] == "等待时长(分钟)"
    call_ui = kinds["call_activity"]["ui"]
    grouped_call = {f for g in call_ui["form_groups"] for f in g["fields"]}
    assert {"binding", "multi_instance"} <= grouped_call
    assert call_ui["field_labels"]["multi_instance.subjects"].startswith("参与主体")


WAIT_SPEC = {
    "code": "wait_api_demo",
    "name": "等待演示",
    "nodes": [
        {"code": "start", "kind": "start"},
        {
            "code": "wait_pay",
            "kind": "wait",
            "name": "等回款",
            "message": {"name": "payment_arrived"},
            "timeout": {"after_minutes": 60},
        },
        {"code": "ok", "kind": "end"},
        {"code": "late", "kind": "end"},
    ],
    "flows": [
        {"source": "start", "target": "wait_pay"},
        {"source": "wait_pay", "target": "ok", "condition": 'wait.result == "matched"'},
        {"source": "wait_pay", "target": "late", "condition": 'wait.result == "elapsed"'},
    ],
}


def test_dry_run_wait_assumptions(client: TestClient) -> None:
    """试运行等待节点：缺省两面都预演；显式假设只推一面（走查补齐批）。"""
    auto = client.post("/api/dry-run", json={"spec": WAIT_SPEC, "variables": {}}).json()
    assert auto["ok"], auto["blocked"]
    via_wait = [s["node"] for s in auto["steps"] if s["via_source"] == "wait_pay"]
    assert via_wait == ["ok", "late"]
    matched = client.post(
        "/api/dry-run",
        json={"spec": WAIT_SPEC, "variables": {}, "assumptions": {"wait_pay": "matched"}},
    ).json()
    assert [s["node"] for s in matched["steps"] if s["via_source"] == "wait_pay"] == ["ok"]


def test_catalog_endpoint(client: TestClient) -> None:
    """/api/catalog（F4）：判断面词汇下发，含中文标签与可用位点。"""
    data = client.get("/api/catalog").json()
    idents = {entry["name"]: entry for entry in data["identifiers"]}
    assert {"initiator", "now", "instance", "prev_task", "task", "wait"} <= set(idents)
    wait_entry = idents["wait"]
    assert wait_entry["label"] == "等待结果"
    assert wait_entry["positions"] == ["condition"]
    functions = {entry["name"]: entry for entry in data["functions"]}
    assert "node" in functions
    assert functions["node"]["label"] == "点名查节点事实"


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
        {
            "code": "countersign_branch",
            "name": "改名了",
            "draft_version": 1,
            "published_version": None,
            "updated_at": listing[0]["updated_at"],
        },
    ]

    got = client.get("/api/definitions/countersign_branch").json()
    assert got["draft_version"] == 1
    assert got["spec"]["name"] == "改名了"
    assert len(got["spec"]["nodes"]) == 6


def test_validate_reports_issue_with_location(client: TestClient) -> None:
    broken = {
        "code": "broken",
        "name": "坏定义",
        "nodes": [
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
        "code": "broken",
        "name": "坏定义",
        "nodes": [
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


# —— dry-run / 公式试算 / 语义 diff / 流程变量 ——


def _branch_spec() -> dict:
    return {
        "code": "dryrun_demo",
        "name": "预演示例",
        "variables": [
            {"name": "amount", "type": "number", "label": "金额", "sample": 20000},
        ],
        "nodes": [
            {"code": "start", "name": "发起", "kind": "start"},
            {"code": "branch", "name": "金额判断", "kind": "branch", "route_mode": "first_match"},
            {"code": "big", "name": "大额审批", "kind": "user_task", "assignee": "'u1'"},
            {"code": "small", "name": "小额审批", "kind": "user_task", "assignee": "'u2'"},
            {"code": "end", "name": "结束", "kind": "end"},
        ],
        "flows": [
            {"source": "start", "target": "branch"},
            {
                "source": "branch",
                "target": "big",
                "condition": "amount > 10000",
            },
            {"source": "branch", "target": "small", "is_default": True},
            {"source": "big", "target": "end"},
            {"source": "small", "target": "end"},
        ],
    }


def test_dry_run_first_match_and_default(client: TestClient) -> None:
    spec = _branch_spec()

    # 大额：命中 amount > 10000 的边
    hit = client.post("/api/dry-run", json={"spec": spec, "variables": {"amount": 20000}}).json()
    assert hit["ok"] is True
    assert [step["node"] for step in hit["steps"]] == ["start", "branch", "big", "end"]
    assert "命中即走" in hit["steps"][2]["via_reason"]

    # 小额：条件不满足 → 默认边
    miss = client.post("/api/dry-run", json={"spec": spec, "variables": {"amount": 100}}).json()
    assert miss["ok"] is True
    assert [step["node"] for step in miss["steps"]] == ["start", "branch", "small", "end"]
    assert "默认边" in miss["steps"][2]["via_reason"]

    # 未提供样例值：条件按不满足处理并告警
    no_var = client.post("/api/dry-run", json={"spec": spec, "variables": {}}).json()
    assert no_var["ok"] is True
    assert any("amount" in w for w in no_var["warnings"])


def test_dry_run_parallel_and_blocked(client: TestClient) -> None:
    # 并行：start 两条无条件出边 → 两支都进路径
    parallel = {
        "code": "dryrun_parallel",
        "name": "并行预演",
        "nodes": [
            {"code": "start", "name": "发起", "kind": "start"},
            {"code": "a", "name": "左支", "kind": "user_task", "assignee": "'u1'"},
            {"code": "b", "name": "右支", "kind": "user_task", "assignee": "'u2'"},
            {"code": "end", "name": "结束", "kind": "end"},
        ],
        "flows": [
            {"source": "start", "target": "a"},
            {"source": "start", "target": "b"},
            {"source": "a", "target": "end"},
            {"source": "b", "target": "end"},
        ],
    }
    result = client.post("/api/dry-run", json={"spec": parallel, "variables": {}}).json()
    assert result["ok"] is True
    assert [step["node"] for step in result["steps"]] == ["start", "a", "b", "end"]

    # 阻断：分支条件不满足且无默认边 → blocked
    spec = _branch_spec()
    spec["flows"] = [f for f in spec["flows"] if f["target"] != "small"]
    blocked = client.post("/api/dry-run", json={"spec": spec, "variables": {"amount": 100}}).json()
    assert blocked["ok"] is False
    assert any("无法路由" in b for b in blocked["blocked"])


def test_formula_preview(client: TestClient) -> None:
    ok = client.post(
        "/api/formula/preview", json={"formula": "amount > 1000", "variables": {"amount": 2000}}
    ).json()
    assert ok == {"ok": True, "result": True}
    bad = client.post(
        "/api/formula/preview", json={"formula": "amount >", "variables": {"amount": 1}}
    ).json()
    assert bad["ok"] is False
    assert "错误" in bad["error"]


def test_diff_reports_changes(client: TestClient, spec02: dict) -> None:
    client.put("/api/definitions/countersign_branch", json=spec02)
    client.post("/api/definitions/countersign_branch/publish")

    # 首次发布语义：从未发布过的 code
    first = client.post("/api/definitions/fresh/diff", json=spec02).json()
    assert first["first_publish"] is True and first["base_version"] is None

    # 改名 + 改字段后的 diff
    changed = json.loads(json.dumps(spec02))
    changed["name"] = "改名后的流程"
    for node in changed["nodes"]:
        if node["kind"] == "user_task":
            node["assignee"] = "u_changed"
            break
    url = "/api/definitions/countersign_branch/diff"
    diff = client.post(url, json=changed).json()
    assert diff["base_version"] == 1
    assert diff["has_changes"] is True
    details = " | ".join(c["detail"] for c in diff["changes"])
    assert "流程名称" in details
    assert "assignee" in details  # 变更条目按字段名报告

    # 无差异
    same = client.post("/api/definitions/countersign_branch/diff", json=spec02).json()
    assert same["has_changes"] is False


def test_variables_roundtrip_and_unknown_reference_warning(client: TestClient) -> None:
    spec = _branch_spec()
    saved = client.put("/api/definitions/dryrun_demo", json=spec)
    assert saved.status_code == 200
    got = client.get("/api/definitions/dryrun_demo").json()
    assert got["spec"]["variables"][0]["name"] == "amount"

    # 声明了变量、条件引用未声明变量 → 校验预警
    warned = json.loads(json.dumps(spec))
    warned["flows"][1]["condition"] = "qty > 5"
    result = client.post("/api/validate", json=warned).json()
    assert any(issue["rule"] == "condition.variable.unknown" for issue in result["issues"])

    # 全部引用都在声明内 → 无该预警（单轨 CEL，无第二格式）
    fine = client.post("/api/validate", json=spec).json()
    assert not any(issue["rule"] == "condition.variable.unknown" for issue in fine["issues"])


def test_formula_preview_value_semantics_and_initiator_placeholder(client):
    """试算面值语义（位点统一）：取人公式返回列表、缺 initiator 注入占位。"""
    r1 = client.post(
        "/api/formula/preview", json={"formula": "amount * 2", "variables": {"amount": 21}}
    ).json()
    assert r1["ok"] and r1["result"] == 42
    r2 = client.post(
        "/api/formula/preview", json={"formula": "manager_of(initiator)", "variables": {}}
    ).json()
    assert not r2["ok"]  # 未注册函数：明确报错（诚实分级）
    r3 = client.post(
        "/api/formula/preview",
        json={"formula": "initiator == 'u_initiator'", "variables": {}},
    ).json()
    assert r3["ok"] and r3["result"] is True  # 占位 initiator 可引用
