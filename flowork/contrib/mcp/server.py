"""MCP 传输壳：mcp lowlevel Server + stdio 入口。

工具层（catalog.py）传输无关；本模块只做三件事——list_tools 回传目录、
call_tool 转 dispatch 结果为 TextContent、stdio 装配。换传输不动工具层。

启动（stdio）：``python -m flowork.contrib.mcp.server``（FLOWORK_DB/FLOWORK_ORG 同 REST）。
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from flowork.api import EngineConfig, FloworkError, ProcessEngine, StaticOrgDirectory
from flowork.contrib.mcp.catalog import (
    UnknownToolError,
    build_tools,
    dispatch,
)

__all__ = ["build_server", "call_tool_result", "create_engine"]


def create_engine() -> ProcessEngine:
    db_path = os.environ.get("FLOWORK_DB", "flowork.db")
    config_kwargs: dict[str, Any] = {"db_file": db_path}
    org_path = os.environ.get("FLOWORK_ORG")
    if org_path:
        config_kwargs["org"] = StaticOrgDirectory.from_yaml(org_path)
    return ProcessEngine(EngineConfig(**config_kwargs))


def _text_of(payload: dict[str, Any]) -> TextContent:
    return TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))


def call_tool_result(
    engine: ProcessEngine, name: str, arguments: dict[str, Any] | None
) -> list[TextContent] | CallToolResult:
    """dispatch 结果转 MCP 返回（同步可直测，传输壳委托本函数）。

    错误通道统一（review P1-1）：业务/参数错误一律 CallToolResult(isError=True)
    ＋统一 JSON 体——修前业务错误 isError=False（「调用成功」），AI 客户端基于
    isError 的错误处理对业务错误完全不触发；三种形状收敛为「isError=True＋JSON」。"""
    try:
        result = dispatch(engine, name, arguments)
    except UnknownToolError as exc:
        payload = {"error": {"code": "UnknownTool", "message": str(exc), "details": {}}}
        return CallToolResult(content=[_text_of(payload)], isError=True)
    except FloworkError as exc:
        payload = {"error": {"code": type(exc).__name__, "message": str(exc), "details": {}}}
        return CallToolResult(content=[_text_of(payload)], isError=True)
    return [_text_of(result)]


def build_server(engine: ProcessEngine) -> Server:
    server = Server("flowork")

    @server.list_tools()  # type: ignore[no-untyped-call]
    async def _list_tools() -> list[Tool]:
        return [
            Tool(name=tool.name, description=tool.description, inputSchema=tool.input_schema)
            for tool in build_tools(engine)
        ]

    @server.call_tool()
    async def _call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[TextContent] | CallToolResult:  # SDK 1.x 两种返回形都收（isError 经后者）
        return call_tool_result(engine, name, arguments)

    return server


def main() -> None:
    import anyio

    engine = create_engine()
    server = build_server(engine)

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    try:
        anyio.run(_run)
    finally:
        engine.close()  # stdio EOF 后释放（对齐 REST shutdown hook，review P2-8）


if __name__ == "__main__":
    main()
