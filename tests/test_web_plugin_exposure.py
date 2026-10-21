"""插件暴露三动作的实证（2026-10-19 插件体系走查定案的落地）：

1. **目录下发**：宿主注册的表达式函数从签名推导目录条目（含中文标签与位点），
   /api/catalog 合并下发——编辑器点选与校验器看同一个世界；
2. **FLOWORK_SETUP**：一个模块把 nodes/vocabulary/handlers 三个钩子全带进
   独立进程（web 与 REST 共用同一装配）；
3. **宿主挂载**：register_designer_routes(app, engine) 与 REST 对称——
   自配引擎的扩展点（函数/handler/自定义节点）全跟引擎走，不需要再配任何东西。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flowork.api import (
    EngineConfig,
    ExpressionFunction,
    FunctionSignature,
    ProcessEngine,
)
from flowork.contrib.web.app import register_designer_routes


class _HostVocabulary:
    """宿主自写的取人/谓词/值函数各一个——覆盖位点推导三档。"""

    def functions(self) -> dict[str, ExpressionFunction]:
        return {
            "host_manager_of": ExpressionFunction(
                FunctionSignature(params=("one_user",), returns="one_user", label="宿主上级"),
                lambda user: "u_boss",
            ),
            "host_is_vip": ExpressionFunction(
                FunctionSignature(params=("one_user",), returns="bool", label="VIP 判定"),
                lambda user: True,
            ),
            "host_score": ExpressionFunction(
                FunctionSignature(params=("string",), returns="number", label="宿主打分"),
                lambda key: 42,
            ),
        }


def _host_engine() -> ProcessEngine:
    """宿主自配引擎：带自定义词汇与 handler——扩展点全挂引擎上。"""
    engine = ProcessEngine(EngineConfig(db_file=":memory:", expression_functions=_HostVocabulary()))
    engine.register_handler("host_send_mail", lambda ctx: None)
    return engine


def _mount(engine: ProcessEngine) -> TestClient:
    """宿主挂载形态：自己的 FastAPI + 自己的引擎，三行代码。"""
    app = FastAPI()
    register_designer_routes(app, engine)
    return TestClient(app, raise_server_exceptions=False)


# —— 动作三：目录下发 ——


def test_catalog_exposes_host_functions_with_positions() -> None:
    """宿主函数进目录：位点按返回类型推导（取人族/谓词/值类型三档）。"""
    with _mount(_host_engine()) as client:
        functions = {f["name"]: f for f in client.get("/api/catalog").json()["functions"]}
    host = functions["host_manager_of"]
    assert host["label"] == "宿主上级"
    assert set(host["positions"]) == {"subjects", "reassign"}
    assert "宿主注册" in host["note"] and "one_user" in host["note"]
    assert set(functions["host_is_vip"]["positions"]) == {"condition", "exit"}
    # 值类型可嵌套任意位
    assert set(functions["host_score"]["positions"]) >= {"condition", "subjects", "mapping_input"}
    # 引擎注入函数仍在
    assert "node" in functions


def test_catalog_positions_drive_condition_editor_filter() -> None:
    """前端连线编辑器只显示 positions 含 condition 的函数——谓词进、纯取人不进。"""
    with _mount(_host_engine()) as client:
        functions = client.get("/api/catalog").json()["functions"]
    condition_names = {f["name"] for f in functions if "condition" in f["positions"]}
    assert "host_is_vip" in condition_names
    assert "host_manager_of" not in condition_names


# —— 动作一：宿主挂载 ——


def test_host_mount_exposes_everything_via_engine() -> None:
    """宿主挂载：函数可模拟、handler 进名录、runtime 报来源——零额外配置。"""
    with _mount(_host_engine()) as client:
        runtime = client.get("/api/runtime").json()
        assert runtime["vocabulary_source"] == "宿主引擎自带"
        assert "host_manager_of" in runtime["vocabulary_functions"]

        handlers = client.get("/api/handlers").json()["handlers"]
        assert "host_send_mail" in handlers

        spec = {
            "code": "host_flow",
            "nodes": [
                {"code": "start", "kind": "start"},
                {
                    "code": "m",
                    "kind": "user_task",
                    "assignee": "host_manager_of(initiator)",
                },
                {"code": "end", "kind": "end"},
            ],
            "flows": [
                {"source": "start", "target": "m"},
                {"source": "m", "target": "end"},
            ],
        }
        resp = client.post(
            "/api/definitions/host_flow/simulate",
            json={"spec": spec, "variables": {}, "start_user": "u_zhang"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["final_state"] == "completed"


def test_host_mount_reports_engine_db_not_ephemeral(tmp_path: Path) -> None:
    """宿主挂载到文件库引擎：session_ephemeral 如实报 False（不假装是内存）。"""
    engine = ProcessEngine(
        EngineConfig(db_file=str(tmp_path / "host.db"), expression_functions=_HostVocabulary())
    )
    with _mount(engine) as client:
        runtime = client.get("/api/runtime").json()
    assert runtime["session_ephemeral"] is False


# —— 动作二：FLOWORK_SETUP 三钩子（独立进程一次配齐）——


def test_flowork_setup_module_bridges_all_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一个 setup 模块带全 nodes/vocabulary/handlers——独立设计器进程生效。"""
    module = tmp_path / "host_setup_a.py"
    module_name = "host_setup_a"
    module.write_text(
        "from flowork.api import ExpressionFunction, FunctionSignature\n"
        "from flowork.contrib._registry import FixedFunctions\n"
        "\n"
        "def vocabulary(org=None):\n"
        "    return FixedFunctions({'setup_fn': ExpressionFunction(\n"
        "        FunctionSignature(params=('one_user',), returns='one_user', label='setup 上级'),\n"
        "        lambda user: 'u_setup_boss',\n"
        "    )})\n"
        "\n"
        "def handlers(engine):\n"
        "    engine.register_handler('setup_action', lambda ctx: None)\n"
        "\n"
        "class ExtraNode:  # 自定义节点类型：Spec 声明\n"
        "    pass\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("FLOWORK_SETUP", module_name)

    from flowork.contrib.assembly import assemble_engine

    engine, source = assemble_engine(":memory:")
    assert module_name in source
    assert "setup_fn" in engine.config.expression_functions.functions()
    assert "setup_action" in engine.handlers.names()

    app = FastAPI()
    register_designer_routes(app, engine)
    with TestClient(app) as client:
        catalog = {f["name"] for f in client.get("/api/catalog").json()["functions"]}
        assert "setup_fn" in catalog
        assert "setup_action" in client.get("/api/handlers").json()["handlers"]


def test_flowork_setup_rest_engine_shares_the_same_assembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REST 独立进程同走 assembly：同一个 setup 在 /v2 面同样生效（缺口四收口）。"""
    module = tmp_path / "host_setup_b.py"
    module_name = "host_setup_b"
    module.write_text(
        "from flowork.api import ExpressionFunction, FunctionSignature\n"
        "from flowork.contrib._registry import FixedFunctions\n"
        "\n"
        "def vocabulary(org=None):\n"
        "    return FixedFunctions({'setup_fn': ExpressionFunction(\n"
        "        FunctionSignature(params=('one_user',), returns='one_user', label='setup 上级'),\n"
        "        lambda user: 'u_setup_boss',\n"
        "    )})\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("FLOWORK_SETUP", module_name)

    from flowork.contrib.rest.app import create_engine

    engine = create_engine(":memory:")
    assert "setup_fn" in engine.config.expression_functions.functions()


def test_setup_priority_over_env_vocabulary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """词汇四级优先：setup.vocabulary > FLOWORK_VOCABULARY。"""
    module = tmp_path / "host_setup_c.py"
    module_name = "host_setup_c"
    module.write_text(
        "from flowork.api import ExpressionFunction, FunctionSignature\n"
        "from flowork.contrib._registry import FixedFunctions\n"
        "\n"
        "def vocabulary(org=None):\n"
        "    return FixedFunctions({'from_setup': ExpressionFunction(\n"
        "        FunctionSignature(params=(), returns='number', label='setup'),\n"
        "        lambda: 1,\n"
        "    )})\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("FLOWORK_SETUP", module_name)
    monkeypatch.setenv("FLOWORK_VOCABULARY", "no_such_module:build")

    from flowork.contrib.assembly import assemble_engine

    engine, source = assemble_engine(":memory:")
    assert "from_setup" in engine.config.expression_functions.functions()
    assert module_name in source


def test_custom_node_kind_via_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """自定义节点类型（第三座桥）：setup.nodes 在引擎构建前注册进注册表。"""
    module = tmp_path / "host_setup_d.py"
    module_name = "host_setup_d"
    module.write_text(
        "from flowork.api import FlowNode\n"
        "from typing import Literal\n"
        "\n"
        "class ApprovalMatrixSpec(FlowNode):\n"
        "    kind: Literal['approval_matrix'] = 'approval_matrix'\n"
        "    matrix: str = ''\n"
        "\n"
        "def nodes(registry):\n"
        "    registry.register('approval_matrix', ApprovalMatrixSpec,\n"
        "                      ui={'name': '审批矩阵', 'category': '任务', 'shape': 'rect'})\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("FLOWORK_SETUP", module_name)

    from flowork.contrib.assembly import assemble_engine

    engine, _ = assemble_engine(":memory:")
    assert "approval_matrix" in engine.config.node_registry.kinds()

    app = FastAPI()
    register_designer_routes(app, engine)
    with TestClient(app, raise_server_exceptions=False) as client:
        spec = {
            "code": "matrix_flow",
            "nodes": [
                {"code": "start", "kind": "start"},
                {"code": "mx", "kind": "approval_matrix", "matrix": "m1"},
                {"code": "end", "kind": "end"},
            ],
            "flows": [
                {"source": "start", "target": "mx"},
                {"source": "mx", "target": "end"},
            ],
        }
        # 自定义 kind 能被 /api/node-kinds 下发（设计器画布可见、表单可生成）
        kinds = {item["kind"] for item in client.get("/api/node-kinds").json()}
        assert "approval_matrix" in kinds
        # 装载不再 UnknownNodeKind
        resp = client.post("/api/validate", json=spec)
        assert resp.status_code == 200, resp.text
