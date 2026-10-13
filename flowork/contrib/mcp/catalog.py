"""MCP 19 工具目录：ToolSpec 注册与 dispatch（传输无关，纯同步）。

设计（openface-plugin-design.md §3）：catalog 层可脱离 stdio 直接测试；
server.py 只做 mcp lowlevel Server 传输壳。全部内核访问经 flowork.api。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flowork.api import ProcessEngine

__all__ = ["TOOL_NAMES", "ToolSpec", "build_tools", "dispatch"]


@dataclass(frozen=True)
class ToolSpec:
    """一个 MCP 工具：名 + 描述 + 入参 JSON Schema + 闭包 handler。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


def build_tools(engine: ProcessEngine) -> list[ToolSpec]:
    """装配 19 工具（spec §13 清单；重名即装配错误）。"""
    from flowork.contrib.mcp.tools_definitions import definition_tools
    from flowork.contrib.mcp.tools_runtime import runtime_tools
    from flowork.contrib.mcp.tools_tasks import task_tools

    tools = [*definition_tools(engine), *runtime_tools(engine), *task_tools(engine)]
    names = [tool.name for tool in tools]
    if len(set(names)) != len(names):
        raise ValueError(f"MCP 工具重名：{sorted(n for n in names if names.count(n) > 1)}")
    return tools


def dispatch(engine: ProcessEngine, name: str, arguments: dict[str, Any] | None) -> Any:
    """按名执行工具；未知工具抛 LookupError（传输壳转 MCP 错误）。"""
    for tool in build_tools(engine):
        if tool.name == name:
            return tool.handler(arguments or {})
    raise LookupError(f"未知 MCP 工具：{name}（已知 {len(build_tools(engine))} 个）")


TOOL_NAMES: frozenset[str] = frozenset(
    {
        "validate_spec",
        "dry_run",
        "deploy",
        "definition_drift",
        "list_definitions",
        "start_instance",
        "query_instance",
        "signal_instance",
        "publish_message",
        "update_instance_variables",
        "list_my_tasks",
        "claim_task",
        "complete_task",
        "reject_task",
        "anticipate_task",
        "pause_instance",
        "resume_instance",
        "list_incidents",
        "resolve_incident",
    }
)
