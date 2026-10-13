"""MCP 工具面快照守卫（openface-plugin-design.md §7）：19 工具名冻结 + 兼容声明。

工具清单即 AI 契约（spec §13）；增删工具=红灯=显式动作（同步本快照 + spec §13）。
"""

from __future__ import annotations

import pytest

import flowork.api
from flowork.api import EngineConfig, ProcessEngine
from flowork.contrib.mcp import REQUIRES_FLOWORK_API, TOOL_NAMES
from flowork.contrib.mcp.catalog import build_tools

# 冻结清单（spec §13：首批 18 + resume_instance【2026-10-13 拍板④】，字母序）
EXPECTED: frozenset[str] = frozenset(
    {
        "anticipate_task",
        "claim_task",
        "complete_task",
        "definition_drift",
        "deploy",
        "dry_run",
        "list_definitions",
        "list_incidents",
        "list_my_tasks",
        "pause_instance",
        "publish_message",
        "query_instance",
        "reject_task",
        "resolve_incident",
        "resume_instance",
        "signal_instance",
        "start_instance",
        "update_instance_variables",
        "validate_spec",
    }
)


def _engine() -> ProcessEngine:
    return ProcessEngine(EngineConfig())


def test_mcp_tool_surface_frozen() -> None:
    tools = build_tools(_engine())
    names = {tool.name for tool in tools}
    assert names == EXPECTED, (
        "MCP 工具增删须显式：同步本快照 + spec §13 + openface-plugin-design.md §3。\n"
        f"新增: {sorted(names - EXPECTED)}\n移除: {sorted(EXPECTED - names)}"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_tool_contract_shape(name: str) -> None:
    """每个工具：有描述、schema 是 object、required ⊆ properties（typed DTO 承诺）。"""
    tool = next(t for t in build_tools(_engine()) if t.name == name)
    assert tool.description
    schema = tool.input_schema
    assert schema.get("type") == "object"
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    assert set(required) <= set(properties), f"{name} required 越界"


def test_tool_names_constant_matches_build() -> None:
    """catalog 导出的 TOOL_NAMES 常量与装配结果一致（防两处手写漂移）。"""
    assert {t.name for t in build_tools(_engine())} == set(TOOL_NAMES)


def test_mcp_plugin_compat_declaration() -> None:
    """插件兼容声明 ≤ 当前门面版本（openface-plugin-design.md §4）。"""
    assert REQUIRES_FLOWORK_API <= flowork.api.API_VERSION
