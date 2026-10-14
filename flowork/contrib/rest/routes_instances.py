"""REST /v2 instances 面：发起/搜索/动作/实例级 signal/变量（spec §12 + 补缺发起端点）。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from flowork.api import NotFoundError, ProcessEngine
from flowork.contrib.rest._common import (
    RestConflictError,
    RestRequestError,
    actor_of,
    dump,
    encode_offset,
    page_request,
    reuse_of,
)

__all__ = ["register_instance_routes", "signal_waiting_instance"]


def register_instance_routes(app: FastAPI, engine: ProcessEngine) -> None:
    runtime = engine.runtime
    history = engine.history

    @app.post("/v2/instances")
    def start_instance(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        code = body.get("code")
        if not code:
            raise RestRequestError("body.code 必填（流程定义编码）")
        instance_id = runtime.start_process(
            str(code),
            start_user=str(body.get("start_user") or actor_of(request, body)),
            variables=body.get("variables"),
            business_key=body.get("business_key"),
            major=body.get("major"),
            revision=body.get("revision"),
            idempotency_key=body.get("idempotency_key"),
            reuse=reuse_of(body.get("reuse")),
        )
        return {"instance_id": instance_id}

    @app.post("/v2/instances/search")
    def instances_search(body: dict[str, Any]) -> dict[str, Any]:
        flt = body.get("filter") or {}
        offset, limit = page_request(body.get("page"))
        # limit+1 余量探测（review P0-1）：修前取数窗口恰好 offset+limit，
        # has_more 恒 False——next_cursor 恒 None，第二页数据不可达
        rows = history.instances(
            state=flt.get("state"),
            definition_code=flt.get("definition_code"),
            offset=offset,
            limit=limit + 1,
            include_simulated=bool(flt.get("include_simulated", False)),
        )
        has_more = len(rows) > limit
        return {
            "items": [dump(row) for row in rows[:limit]],
            "next_cursor": encode_offset(offset + limit) if has_more else None,
        }

    def _state_action(action: str) -> None:
        method = getattr(runtime, f"{action}_process")

        def handler(request: Request, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
            method(instance_id, actor=actor_of(request, body), reason=str(body.get("reason") or ""))
            return {"instance_id": instance_id, "action": action}

        app.post(f"/v2/instances/{{instance_id}}/{action}")(handler)

    for _action in ("pause", "resume", "cancel", "terminate"):
        _state_action(_action)

    @app.post("/v2/instances/{instance_id}/signal")
    def signal(instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return signal_waiting_instance(engine, instance_id, body)

    @app.get("/v2/instances/{instance_id}/variables")
    def variables_of(instance_id: str) -> dict[str, Any]:
        return runtime.variables_of(instance_id)

    @app.patch("/v2/instances/{instance_id}/variables")
    def update_variables(
        request: Request, instance_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        updates = body.get("updates")
        if not isinstance(updates, dict):
            updates = {k: v for k, v in body.items() if k not in ("actor", "reason")}
        return runtime.update_variables(
            instance_id,
            updates,
            actor=actor_of(request, body),
            reason=str(body.get("reason") or ""),
        )

    @app.get("/v2/instances/{instance_id}/variables/history")
    def variable_history(
        instance_id: str, name: str | None = None, after: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        offset, size = page_request({"after": after, "limit": limit})
        rows = [
            dump(row)
            for row in runtime.variable_history(
                instance_id, name=name, limit=size + 1, offset=offset
            )
        ]
        has_more = len(rows) > size
        return {
            "items": rows[:size],
            "next_cursor": encode_offset(offset + size) if has_more else None,
        }


def signal_waiting_instance(
    engine: ProcessEngine, instance_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """实例级 signal：按本实例 open 订阅的 (name, key) 同步关联，回执核验防串单（设计 §2.4）。"""
    runtime = engine.runtime
    runtime.get_instance(instance_id)  # 不存在 → InstanceNotFoundError（404）
    name = body.get("name")
    if not name:
        raise NotFoundError("body.name 必填（等待的消息名）")
    subscriptions = [
        row for row in engine.messages.waiting(name=str(name)) if row["instance_id"] == instance_id
    ]
    if not subscriptions:
        raise NotFoundError(f"实例 {instance_id} 没有等待消息 {name!r} 的 open 订阅")
    key = str(body.get("correlation_key") or subscriptions[0]["correlation_key"] or "")
    receipt = engine.messages.correlate_message(
        str(name),
        correlation_key=key,
        payload=body.get("payload"),
        message_id=body.get("message_id"),
        instance_id=instance_id,  # 实例定向（P0-2 防串单）：只消费本实例的订阅
    )
    if receipt is None or receipt.get("instance_id") != instance_id:
        # 定向关联下正常不会进此分支；剩余可达路径＝message_id 已被其他实例
        # 消费后的幂等回放——回执核验照旧拦截，错误带上实际消费者
        raise RestConflictError(
            f"消息 {name!r} 未被实例 {instance_id} 消费（回执：{receipt or '无人在等'}）"
        )
    return receipt
