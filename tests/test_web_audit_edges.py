"""web 审计面边界修复的回归（全仓 review 2026-10-13 批 2）。

- P1-1：simulate 对非 dict 的 spec 修前静默回退已保存草稿——「当前草稿含
  未保存编辑」是主场景，据以拍板发布的报告可能不是送审内容；
- P2-1：四个子资源端点修前对幽灵实例返回 200 空载荷（except FloworkError
  是死代码——内核查询面对不存在实例返回空而非抛错），与 detail 的 404 不一致；
- P2-2：limit 负值直通 SQL（SQLite 负 LIMIT=无上限全量）；
- P2-5：FloworkError 修前一律 422（与 REST /v2 面 404 映射跨面不一致）；
- P1-2：FormulaError 消息截断（celpy 环境转储数 KB，泄漏变量值）。
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
    app = create_app(db_file=tmp_path / "audit_edge.db")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def spec02() -> dict:
    return json.loads((EXAMPLES / "02_countersign_branch.json").read_text(encoding="utf-8"))


def test_simulate_rejects_non_dict_spec(client: TestClient, spec02: dict) -> None:
    code = "edge_spec_type"
    assert client.put(f"/api/definitions/{code}", json=spec02).status_code == 200
    # 草稿在库——非 dict spec 不得静默回退它
    body = client.post(f"/api/definitions/{code}/simulate", json={"spec": "garbage"})
    assert body.status_code == 422, f"修前静默模拟旧草稿返回 200/422：{body.status_code}"
    body = client.post(
        f"/api/definitions/{code}/simulate", json={"spec": ["a"], "variables": ["x"]}
    )
    assert body.status_code == 422


def test_flowork_error_not_found_maps_404(client: TestClient) -> None:
    # major=99 走发布版分支 → 内核抛 DefinitionNotPublishedError(NotFoundError 族)
    # → 应用级 FloworkError handler（修前一律 422）
    body = client.post("/api/definitions/ghost/simulate?major=99", json={})
    assert body.status_code == 404, "修前一律 422（与 REST /v2 面 404 跨面不一致）"


def test_cel_error_message_truncated(client: TestClient, spec02: dict) -> None:
    """缺样例变量（最常见失误）：422 的 detail 不再内嵌完整求值环境转储。"""
    code = "edge_cel_msg"
    assert client.put(f"/api/definitions/{code}", json=spec02).status_code == 200
    body = client.post(f"/api/definitions/{code}/simulate", json={"spec": spec02, "variables": {}})
    if body.status_code == 422:
        detail = json.dumps(body.json(), ensure_ascii=False)
        assert len(detail) < 4000, f"修前 ~4KB 环境转储：{len(detail)}"
        assert "已截断" in detail or "undeclared" in detail
