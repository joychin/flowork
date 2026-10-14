"""MCP 19 工具目录：ToolSpec 注册与 dispatch（传输无关，纯同步）。

设计（openface-plugin-design.md §3）：catalog 层可脱离 stdio 直接测试；
server.py 只做 mcp lowlevel Server 传输壳。全部内核访问经 flowork.api。
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flowork.api import FloworkError, ProcessEngine

__all__ = [
    "TOOL_NAMES",
    "McpToolError",
    "ToolSpec",
    "UnknownToolError",
    "build_tools",
    "dispatch",
]


class McpToolError(FloworkError):
    """工具层参数错误（缺必填/非法枚举/类型不符）——区别于内核业务错误
    （review P1-8：修前用 LookupError 表达被传输壳误标 UnknownTool）。"""


class UnknownToolError(LookupError):
    """未知工具名（LookupError 子类保旧兼容；传输壳据此转 UnknownTool 码）。"""


@dataclass(frozen=True)
class ToolSpec:
    """一个 MCP 工具：名 + 描述 + 入参 JSON Schema + 闭包 handler。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


_TOOLS_CACHE: weakref.WeakKeyDictionary[ProcessEngine, list[ToolSpec]] = weakref.WeakKeyDictionary()


def build_tools(engine: ProcessEngine) -> list[ToolSpec]:
    """装配 19 工具（spec §13 清单；重名即装配错误）。

    进程内缓存（review P2-7）：修前 dispatch 每次重建 19 个闭包、未知工具
    路径重建两次——engine 生命周期内工具面不变，WeakKeyDictionary 随引擎回收。"""
    cached = _TOOLS_CACHE.get(engine)
    if cached is not None:
        return cached
    from flowork.contrib.mcp.tools_definitions import definition_tools
    from flowork.contrib.mcp.tools_runtime import runtime_tools
    from flowork.contrib.mcp.tools_tasks import task_tools

    tools = [*definition_tools(engine), *runtime_tools(engine), *task_tools(engine)]
    names = [tool.name for tool in tools]
    if len(set(names)) != len(names):
        raise ValueError(f"MCP 工具重名：{sorted(n for n in names if names.count(n) > 1)}")
    _TOOLS_CACHE[engine] = tools
    return tools


def dispatch(engine: ProcessEngine, name: str, arguments: dict[str, Any] | None) -> Any:
    """按名执行工具；未知工具抛 UnknownToolError（传输壳转 UnknownTool 码）。"""
    tools = build_tools(engine)
    for tool in tools:
        if tool.name == name:
            return tool.handler(arguments or {})
    raise UnknownToolError(f"未知 MCP 工具：{name}（已知 {len(tools)} 个）")


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
