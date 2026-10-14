"""MCP 运行时组工具：实例发起/查询/signal/pause、变量、消息、故障单（9 个）。"""

from __future__ import annotations

from typing import Any, Literal, cast

from flowork.api import NotFoundError, ProcessEngine
from flowork.contrib.mcp.catalog import (
    McpConflictError,
    McpToolError,
    ToolSpec,
    page_window,
    require_actor,
)

__all__ = ["runtime_tools"]


def _signal_instance(engine: ProcessEngine, instance_id: str, args: dict[str, Any]) -> Any:
    """实例级 signal：按本实例 open 订阅关联 + 回执核验（与 REST /v2 同语义；
    三面互不复用代码，故在此重复实现——跨插件 import 会被公开面纪律拦）。"""
    engine.runtime.get_instance(instance_id)  # 不存在 → InstanceNotFoundError
    name = args.get("name")
    if not name:
        raise McpToolError("name 必填（等待的消息名）")
    subscriptions = [
        row for row in engine.messages.waiting(name=str(name)) if row["instance_id"] == instance_id
    ]
    if not subscriptions:
        raise NotFoundError(f"实例 {instance_id} 没有等待消息 {name!r} 的 open 订阅")
    key = str(args.get("correlation_key") or subscriptions[0]["correlation_key"] or "")
    receipt = engine.messages.correlate_message(
        str(name),
        correlation_key=key,
        payload=args.get("payload"),
        message_id=args.get("message_id"),
        instance_id=instance_id,  # 实例定向（防串单，与 REST /v2 同语义）
    )
    if receipt is None or receipt.get("instance_id") != instance_id:
        # review P1-11：修前 NotFoundError——冲突被标「不存在」，AI 朝错误方向排查
        raise McpConflictError(
            f"消息 {name!r} 未被实例 {instance_id} 消费（回执：{receipt or '无人在等'}）"
        )
    return receipt


def runtime_tools(engine: ProcessEngine) -> list[ToolSpec]:
    runtime = engine.runtime
    messages = engine.messages

    def start_instance(args: dict[str, Any]) -> Any:
        code = args.get("code")
        if not code:
            raise McpToolError("code 必填（流程定义编码）")
        reuse = str(args.get("reuse") or "reject")
        if reuse not in ("reject", "return_existing", "allow_duplicate"):
            raise NotFoundError(f"reuse 不合法：{reuse!r}")
        actor = require_actor(args)
        return {
            "instance_id": runtime.start_process(
                str(code),
                start_user=str(args.get("start_user") or actor),
                variables=args.get("variables"),
                business_key=args.get("business_key"),
                major=args.get("major"),
                revision=args.get("revision"),
                idempotency_key=args.get("idempotency_key"),
                reuse=cast("Literal['reject', 'return_existing', 'allow_duplicate']", reuse),
            )
        }

    def query_instance(args: dict[str, Any]) -> Any:
        instance_id = str(args.get("instance_id") or "")
        instance = runtime.get_instance(instance_id)
        return {
            "instance": instance.model_dump(mode="json"),
            "variables": runtime.variables_of(instance_id),
        }

    def signal_instance(args: dict[str, Any]) -> Any:
        return _signal_instance(engine, str(args.get("instance_id") or ""), args)

    def pause_instance(args: dict[str, Any]) -> Any:
        instance_id = str(args.get("instance_id") or "")
        runtime.pause_process(
            instance_id, require_actor(args), reason=str(args.get("reason") or "")
        )
        # state 读回而非硬编码（review P2-9：与实际终态一致）
        return {"instance_id": instance_id, "state": runtime.get_instance(instance_id).state}

    def resume_instance(args: dict[str, Any]) -> Any:
        instance_id = str(args.get("instance_id") or "")
        runtime.resume_process(
            instance_id, require_actor(args), reason=str(args.get("reason") or "")
        )
        return {"instance_id": instance_id, "state": runtime.get_instance(instance_id).state}

    def update_instance_variables(args: dict[str, Any]) -> Any:
        updates = args.get("updates")
        if not isinstance(updates, dict):
            raise McpToolError("updates 必填（{name: value} 合并写）")
        return runtime.update_variables(
            str(args.get("instance_id") or ""),
            updates,
            actor=require_actor(args),
            reason=str(args.get("reason") or ""),
        )

    def publish_message(args: dict[str, Any]) -> Any:
        name = args.get("name")
        if not name:
            raise McpToolError("name 必填")
        optional: dict[str, Any] = {}
        if args.get("message_id"):
            optional["message_id"] = args["message_id"]
        if args.get("ttl_seconds") is not None:
            ttl = args["ttl_seconds"]
            if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
                raise McpToolError(f"ttl_seconds 不合法（正整数，秒）：{ttl!r}")
            optional["ttl_seconds"] = ttl
        matched = messages.publish_message(
            str(name),
            correlation_key=str(args.get("correlation_key") or ""),
            payload=args.get("payload"),
            **optional,
        )
        return {"name": name, "matched": matched}

    def list_incidents(args: dict[str, Any]) -> Any:
        return page_window(args, runtime.list_incidents(args.get("instance_id")))

    def resolve_incident(args: dict[str, Any]) -> Any:
        incident_id = str(args.get("incident_id") or "")
        runtime.resolve_incident(
            incident_id,
            action=str(args.get("action") or "resume"),
            actor=str(args.get("actor") or "mcp"),
        )
        return {"incident_id": incident_id, "resolved": True}

    return [
        ToolSpec(
            name="start_instance",
            description="发起流程实例（定义快照；幂等三策略）",
            input_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "actor": {"type": "string"},
                    "start_user": {"type": "string"},
                    "variables": {"type": "object"},
                    "business_key": {"type": "string"},
                    "major": {"type": "integer"},
                    "revision": {"type": "integer"},
                    "idempotency_key": {"type": "string"},
                    "reuse": {
                        "type": "string",
                        "enum": ["reject", "return_existing", "allow_duplicate"],
                    },
                },
                "required": ["code", "actor"],
            },
            handler=start_instance,
        ),
        ToolSpec(
            name="query_instance",
            description="实例概要 + 变量值全集（只读）",
            input_schema={
                "type": "object",
                "properties": {"instance_id": {"type": "string"}},
                "required": ["instance_id"],
            },
            handler=query_instance,
        ),
        ToolSpec(
            name="signal_instance",
            description="向指定实例的等待节点投递消息（回执核验防串单）",
            input_schema={
                "type": "object",
                "properties": {
                    "instance_id": {"type": "string"},
                    "name": {"type": "string"},
                    "correlation_key": {"type": "string"},
                    "payload": {"type": "object"},
                    "message_id": {"type": "string"},
                },
                "required": ["instance_id", "name"],
                # 多实例等同一信号时本工具只消费目标实例（防串单）
            },
            handler=signal_instance,
        ),
        ToolSpec(
            name="pause_instance",
            description="暂停实例（timer 冻结；消息落缓冲）",
            input_schema={
                "type": "object",
                "properties": {
                    "instance_id": {"type": "string"},
                    "actor": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["instance_id", "actor"],
            },
            handler=pause_instance,
        ),
        ToolSpec(
            name="resume_instance",
            description="恢复暂停实例（timer 顺延；补消费挂起期缓冲消息）",
            input_schema={
                "type": "object",
                "properties": {
                    "instance_id": {"type": "string"},
                    "actor": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["instance_id", "actor"],
            },
            handler=resume_instance,
        ),
        ToolSpec(
            name="update_instance_variables",
            description="外部受控改值（三门槛：白名单/类型/非终态；发 variables_updated）",
            input_schema={
                "type": "object",
                "properties": {
                    "instance_id": {"type": "string"},
                    "updates": {"type": "object"},
                    "actor": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["instance_id", "updates", "actor"],
            },
            handler=update_instance_variables,
        ),
        ToolSpec(
            name="publish_message",
            description="发布消息（命中订阅立即关联；否则入缓冲等 TTL）",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "correlation_key": {"type": "string"},
                    "payload": {"type": "object"},
                    "message_id": {"type": "string"},
                    "ttl_seconds": {"type": "integer"},
                },
                "required": ["name"],
            },
            handler=publish_message,
        ),
        ToolSpec(
            name="list_incidents",
            description="故障单列表（open/resolved，可按实例过滤；分页返回 items/total）",
            input_schema={
                "type": "object",
                "properties": {
                    "instance_id": {"type": "string"},
                    "limit": {"type": "integer", "description": "1~500，缺省 50"},
                    "offset": {"type": "integer", "description": "非负，缺省 0"},
                },
            },
            handler=list_incidents,
        ),
        ToolSpec(
            name="resolve_incident",
            description="修复故障单（五动作：resume/cancel/retry/skip/fail）",
            input_schema={
                "type": "object",
                "properties": {
                    "incident_id": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["resume", "cancel", "retry", "skip", "fail"],
                    },
                    "actor": {"type": "string"},
                },
                "required": ["incident_id", "actor"],
            },
            handler=resolve_incident,
        ),
    ]
