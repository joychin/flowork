"""MCP 19 工具面插件（AI 工具面）。

分层：catalog（ToolSpec 注册与 dispatch，传输无关）+ server（mcp lowlevel 传输壳）。
全部内核访问经 flowork.api——tests/test_projection_surface.py 与 import-linter 强制。
"""

from __future__ import annotations

from flowork.contrib.mcp.catalog import TOOL_NAMES, ToolSpec, build_tools, dispatch
from flowork.contrib.mcp.server import build_server, create_engine

__all__ = ["TOOL_NAMES", "ToolSpec", "build_server", "build_tools", "create_engine", "dispatch"]

# 兼容声明：本插件依赖的门面契约版本；测试断言 ≤ flowork.api.API_VERSION。
REQUIRES_FLOWORK_API = "1"
