"""设计器的表达式函数装配：宿主自定义取人函数必须能带进设计器进程。

背景：设计器是独立进程，宿主的 CEL 函数住在宿主 Python 代码里。不把它带进来，
任何用了 ``manager_of`` / 自定义取人函数的流程连模拟都跑不起来（求值报未声明函数）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flowork.api import ExpressionFunction, FunctionSignature
from flowork.contrib.web.app import create_app
from flowork.contrib.web.vocabulary import load_vocabulary

EXAMPLES = Path(__file__).parent.parent / "examples"


def _custom_spec() -> dict[str, Any]:
    """取人位用自定义函数 manager_of(initiator)。"""
    return {
        "code": "custom_fn",
        "name": "自定义取人函数",
        "nodes": [
            {"code": "start", "name": "发起", "kind": "start"},
            {
                "code": "m",
                "name": "经理审批",
                "kind": "user_task",
                "assignee": "manager_of(initiator)",
            },
            {"code": "end", "name": "结束", "kind": "end"},
        ],
        "flows": [
            {"source": "start", "target": "m"},
            {"source": "m", "target": "end"},
        ],
    }


class _StubVocabulary:
    """一个最小端口：把任何人映射到固定上级（模拟宿主自写的取人函数）。"""

    def functions(self) -> dict[str, ExpressionFunction]:
        return {
            "manager_of": ExpressionFunction(
                FunctionSignature(params=("one_user",), returns="one_user", label="上级"),
                lambda user: "u_boss",
            )
        }


def test_explicit_vocabulary_makes_custom_function_work() -> None:
    """显式传入端口：自定义函数可求值，模拟跑通并解析出人。"""
    app = create_app(vocabulary=_StubVocabulary())
    with TestClient(app, raise_server_exceptions=False) as client:
        runtime = client.get("/api/runtime").json()
        assert runtime["vocabulary_functions"] == ["manager_of"]
        assert "create_app" in runtime["vocabulary_source"]

        resp = client.post(
            "/api/definitions/custom_fn/simulate",
            json={"spec": _custom_spec(), "variables": {}, "start_user": "u_zhang"},
        )
        assert resp.status_code == 200, resp.text
        report = resp.json()
        assert report["final_state"] == "completed"
        assert [d["assignee"] for d in report["decisions"]] == ["u_boss"]


def test_without_vocabulary_custom_function_fails_loudly() -> None:
    """未装配：模拟明确失败（不是静默跑通）——避免「设计器绿、线上挂」。"""
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/runtime").json()["vocabulary_functions"] == []
        resp = client.post(
            "/api/definitions/custom_fn/simulate",
            json={"spec": _custom_spec(), "variables": {}, "start_user": "u_zhang"},
        )
        assert resp.status_code == 422
        assert "未声明" in resp.text or "undeclared" in resp.text


def test_org_directory_installs_default_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    """配了组织目录即装 contrib.cel_vocabulary——与宿主标准写法一致。"""
    monkeypatch.setenv("FLOWORK_ORG", str(EXAMPLES / "org.yaml"))
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        runtime = client.get("/api/runtime").json()
        assert runtime["vocabulary_functions"] == [
            "has_role",
            "manager_of",
            "users_in_org",
            "users_with_role",
        ]
        # u_zhang 的上级是 u_bob；但 org.yaml 里有 u_bob→u_dave 的委托，取人生效
        resp = client.post(
            "/api/definitions/custom_fn/simulate",
            json={"spec": _custom_spec(), "variables": {}, "start_user": "u_zhang"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["final_state"] == "completed"


def test_start_user_flows_into_initiator() -> None:
    """发起人必须能指定：initiator 是取人公式最常用的输入。

    缺省 simulator 不在组织目录里，取人解析会空——这是正确行为（明明解析不出人
    却跑通才是 bug）；指定真实用户后应当跑通。
    """
    app = _org_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        default = client.post(
            "/api/definitions/custom_fn/simulate", json={"spec": _custom_spec(), "variables": {}}
        )
        assert default.status_code == 422
        assert "没有可用参与者" in default.text

        explicit = client.post(
            "/api/definitions/custom_fn/simulate",
            json={"spec": _custom_spec(), "variables": {}, "start_user": "u_zhang"},
        )
        assert explicit.status_code == 200, explicit.text


def test_start_user_must_be_string() -> None:
    app = _org_app()
    with TestClient(app) as client:
        resp = client.post(
            "/api/definitions/custom_fn/simulate",
            json={"spec": _custom_spec(), "variables": {}, "start_user": 42},
        )
        assert resp.status_code == 422
        assert "start_user" in resp.json()["detail"]


def test_vocabulary_env_module_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量形态：模块:工厂。用临时模块验证真能导入并调用。"""
    module = tmp_path / "host_vocab.py"
    module.write_text(
        "from flowork.api import ExpressionFunction, FunctionSignature\n"
        "from flowork.contrib._registry import FixedFunctions\n"
        "\n"
        "def build():\n"
        "    return FixedFunctions({'manager_of': ExpressionFunction(\n"
        "        FunctionSignature(params=('one_user',), returns='one_user', label='上级'),\n"
        "        lambda user: 'u_env_boss',\n"
        "    )})\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("FLOWORK_VOCABULARY", "host_vocab:build")
    port, source = load_vocabulary()
    assert port is not None
    assert source == "FLOWORK_VOCABULARY=host_vocab:build"
    assert sorted(port.functions()) == ["manager_of"]


def test_vocabulary_env_bad_spec_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    from flowork.api import FloworkError

    monkeypatch.setenv("FLOWORK_VOCABULARY", "只写模块名没有工厂")
    with pytest.raises(FloworkError, match="模块路径:工厂函数名"):
        load_vocabulary()


def test_vocabulary_rejects_non_port(monkeypatch: pytest.MonkeyPatch) -> None:
    from flowork.api import FloworkError

    with pytest.raises(FloworkError, match="ExpressionFunctions"):
        load_vocabulary(explicit=object())  # type: ignore[arg-type]


def _org_app():
    """带组织目录的应用（会话内装配 contrib 词汇）。"""
    import os

    from flowork.contrib.web.app import create_app as build

    previous = os.environ.get("FLOWORK_ORG")
    os.environ["FLOWORK_ORG"] = str(EXAMPLES / "org.yaml")
    try:
        return build()
    finally:
        if previous is None:
            os.environ.pop("FLOWORK_ORG", None)
        else:
            os.environ["FLOWORK_ORG"] = previous


def test_runtime_reports_source_even_when_absent() -> None:
    """未装配时也要给出可读的来源说明（前端据此提示要去配什么）。"""
    app = create_app()
    with TestClient(app) as client:
        runtime = client.get("/api/runtime").json()
        assert runtime["vocabulary_functions"] == []
        assert "未装配" in runtime["vocabulary_source"]


def test_example_org_yaml_exists() -> None:
    """文档里让用户跑的 FLOWORK_ORG=examples/org.yaml 必须真的在。"""
    assert (EXAMPLES / "org.yaml").is_file()
    data = (EXAMPLES / "org.yaml").read_text(encoding="utf-8")
    assert "u_zhang" in data


def test_simulate_start_user_kept_out_of_controls() -> None:
    """start_user 是 body 顶层参数，不是 controls 的键（形状别混）。"""
    raw = json.loads((EXAMPLES / "04_expense_claim.json").read_text(encoding="utf-8"))
    app = _org_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/definitions/expense_claim/simulate",
            json={"spec": raw, "variables": {"amount": 100}, "start_user": "u_zhang"},
        )
        assert resp.status_code == 200, resp.text
