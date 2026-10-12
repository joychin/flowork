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
from mcp.types import TextContent, Tool

from flowork.api import EngineConfig, FloworkError, ProcessEngine, StaticOrgDirectory
from flowork.contrib.mcp.catalog import build_tools, dispatch

__all__ = ["build_server", "create_engine"]


def create_engine() -> ProcessEngine:
    db_path = os.environ.get("FLOWORK_DB", "flowork.db")
    config_kwargs: dict[str, Any] = {"db_file": db_path}
    org_path = os.environ.get("FLOWORK_ORG")
    if org_path:
        config_kwargs["org"] = StaticOrgDirectory.from_yaml(org_path)
    return ProcessEngine(EngineConfig(**config_kwargs))


def build_server(engine: ProcessEngine) -> Server:
    server = Server("flowork")

    @server.list_tools()  # type: ignore[no-untyped-call]
    async def _list_tools() -> list[Tool]:
        return [
            Tool(name=tool.name, description=tool.description, inputSchema=tool.input_schema)
            for tool in build_tools(engine)
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        try:
            result = dispatch(engine, name, arguments)
        except LookupError as exc:
            result = {"error": {"code": "UnknownTool", "message": str(exc), "details": {}}}
        except FloworkError as exc:
            result = {"error": {"code": type(exc).__name__, "message": str(exc), "details": {}}}
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]

    return server


def main() -> None:
    import anyio

    engine = create_engine()
    server = build_server(engine)

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(_run)


if __name__ == "__main__":
    main()
