"""REST /v2 tasks 面：search + 七动作 + anticipate。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from flowork.api import ProcessEngine
from flowork.contrib.rest._common import (
    RestRequestError,
    actor_of,
    dump,
    page_payload,
    page_request,
)

__all__ = ["register_task_routes"]

# list_tasks 的 state 词表（None=全部）：拼错即 422，不静默空列表
_TASK_STATES = ("ready", "completed", "cancelled")


def register_task_routes(app: FastAPI, engine: ProcessEngine) -> None:
    task = engine.task

    @app.post("/v2/tasks/search")
    def tasks_search(body: dict[str, Any]) -> dict[str, Any]:
        flt = body.get("filter") or {}
        state = flt.get("state", "ready")
        if state is not None and state not in _TASK_STATES:
            # 修前拼错静默空列表（AI 误判「无待办」——MCP 面修过同类，REST 补齐）
            raise RestRequestError(
                f"filter.state 不合法：{state!r}（可选 {', '.join(_TASK_STATES)} 或 null）"
            )
        rows = task.list_tasks(
            instance_id=flt.get("instance_id"),
            assignee=flt.get("assignee"),
            candidate=flt.get("candidate"),
            state=state,
        )
        offset, limit = page_request(body.get("page"))
        return page_payload([dump(row) for row in rows], offset, limit)

    @app.post("/v2/tasks/{task_id}/claim")
    def claim(task_id: str, body: dict[str, Any]) -> Any:
        return dump(task.claim(task_id, str(body.get("user") or "")))

    @app.post("/v2/tasks/{task_id}/unclaim")
    def unclaim(task_id: str, body: dict[str, Any]) -> Any:
        return dump(task.unclaim(task_id, str(body.get("user") or "")))

    @app.post("/v2/tasks/{task_id}/complete")
    def complete(request: Request, task_id: str, body: dict[str, Any]) -> Any:
        variables = body.get("variables")
        return dump(
            task.complete(
                task_id,
                actor_of(request, body),
                comment=str(body.get("comment") or ""),
                variables=variables if isinstance(variables, dict) else None,
                idempotency_key=body.get("idempotency_key"),
            )
        )

    @app.post("/v2/tasks/{task_id}/reject")
    def reject(request: Request, task_id: str, body: dict[str, Any]) -> Any:
        return dump(
            task.reject(
                task_id,
                actor_of(request, body),
                comment=str(body.get("comment") or ""),
                target=body.get("target"),
            )
        )

    @app.post("/v2/tasks/{task_id}/transfer")
    def transfer(request: Request, task_id: str, body: dict[str, Any]) -> Any:
        to_user = str(body.get("to_user") or "")
        if not to_user:
            # 缺 to_user 曾 200 成功并产出 assignee="" 且无候选关联的
            # 孤儿任务（claim/unclaim/complete 全路被拒、无故障单）——入口拦 422
            raise RestRequestError("body.to_user 必填（转办目标裸 id）")
        return dump(
            task.transfer(
                task_id,
                to_user,
                actor_of(request, body),
                comment=str(body.get("comment") or ""),
            )
        )

    @app.post("/v2/tasks/{task_id}/assist")
    def assist(request: Request, task_id: str, body: dict[str, Any]) -> Any:
        users = body.get("users")
        rows = task.assist(
            task_id,
            users if isinstance(users, list) else [],
            actor_of(request, body),
            comment=str(body.get("comment") or ""),
        )
        return [dump(row) for row in rows]

    @app.post("/v2/tasks/{task_id}/complete_assist")
    def complete_assist(request: Request, task_id: str, body: dict[str, Any]) -> Any:
        task.complete_assist(
            task_id, actor_of(request, body), comment=str(body.get("comment") or "")
        )
        return {"task_id": task_id, "completed": True}

    @app.post("/v2/tasks/{task_id}/anticipate")
    def anticipate(task_id: str, body: dict[str, Any]) -> Any:
        return dump(task.anticipate(task_id, outcome=str(body.get("outcome") or "agree")))
