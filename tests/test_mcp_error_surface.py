"""MCP 错误面修复的回归（全仓 review 2026-10-13 批 1 P1-1/P1-8 + P2-7）。

- 业务/参数错误修前以 isError=False 的正常文本返回（协议层「调用成功」），
  AI 客户端基于 isError 的错误处理对业务错误不触发；修后统一
  CallToolResult(isError=True)＋统一 JSON 体；
- 参数必填错修前抛 LookupError 被传输壳误标 UnknownTool（工具明明存在）；
- dispatch 未知工具专用 UnknownToolError（LookupError 子类保旧兼容）；
- build_tools 进程内缓存（修前 dispatch 每次重建 19 个闭包）。
"""

from __future__ import annotations

import pytest
from mcp.types import CallToolResult

from flowork.api import FlowBuilder, FloworkError, ProcessEngine
from flowork.contrib.mcp.catalog import McpToolError, UnknownToolError, dispatch
from flowork.contrib.mcp.server import call_tool_result


def _engine() -> ProcessEngine:
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("mcperr")
        .start()
        .user_task("a", assignee="'u1'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    return engine


def test_unknown_tool_raises_dedicated_error():
    engine = _engine()
    with pytest.raises(UnknownToolError) as exc_info:
        dispatch(engine, "no_such_tool", {})
    assert isinstance(exc_info.value, LookupError)  # 旧兼容：仍是 LookupError 子类
    assert "已知 19 个" in str(exc_info.value)


def test_param_error_is_mcp_tool_error_not_lookup():
    engine = _engine()
    with pytest.raises(McpToolError, match="assignee 必填"):
        dispatch(engine, "list_my_tasks", {})


def test_business_error_returns_is_error_true():
    """业务错误（不存在的实例）＝isError=True＋统一 JSON（修前 isError=False）。"""
    engine = _engine()
    result = call_tool_result(engine, "query_instance", {"instance_id": "ghost"})
    assert isinstance(result, CallToolResult)
    assert result.isError is True
    import json

    payload = json.loads(result.content[0].text)
    assert payload["error"]["code"] == "InstanceNotFoundError"


def test_param_error_returns_is_error_true_with_stable_code():
    engine = _engine()
    result = call_tool_result(engine, "list_my_tasks", {})
    assert isinstance(result, CallToolResult)
    assert result.isError is True
    import json

    payload = json.loads(result.content[0].text)
    assert payload["error"]["code"] == "McpToolError"


def test_success_path_stays_plain_content():
    engine = _engine()
    result = call_tool_result(
        engine, "start_instance", {"code": "mcperr", "actor": "u1", "start_user": "u1"}
    )
    assert not isinstance(result, CallToolResult)  # 正常路径仍为 list[TextContent]
    assert len(result) == 1


def test_flowork_error_still_recognized():
    """内核业务错误类型不被吞：McpToolError/UnknownToolError 均为 FloworkError/LookupError 家族。"""
    assert issubclass(McpToolError, FloworkError)
    engine = _engine()
    with pytest.raises(FloworkError):
        dispatch(engine, "start_instance", {})


def test_typed_spec_error_is_tool_error_not_protocol_error():
    """节点字段类型错走统一错误通道（2026-10-21 内核 review P1-13 回归）。

    修前 _load 不接 pydantic ValidationError：裸异常穿出 call_tool_result
    → mcp lowlevel 转 JSON-RPC 协议错 code=0 + 数 KB pydantic 转储。
    """

    engine = _engine()
    from flowork.api import DefinitionError
    from flowork.definitions.definition import dump_definition

    data = dump_definition(engine.repository.get_definition("mcperr"))
    data["nodes"][1]["assignee"] = 123  # 类型错
    # 内核归一为 DefinitionError 族 → call_tool_result 的 FloworkError 通道
    # （isError=True + error.code），不再是裸 pydantic 协议错
    with pytest.raises(DefinitionError, match="节点结构不合法"):
        dispatch(engine, "validate_spec", {"spec": data})


def test_resolve_incident_requires_actor():
    """resolve_incident 缺 actor 拒绝（2026-10-21 内核 review P1-14 回归）。

    修前缺省 "mcp"——唯一没走 require_actor 的写工具，审计归因退化为常量。
    """
    from flowork.contrib.mcp.catalog import McpToolError

    engine = _engine()
    with pytest.raises(McpToolError, match="actor 必填"):
        dispatch(engine, "resolve_incident", {"incident_id": "ghost", "action": "resume"})
