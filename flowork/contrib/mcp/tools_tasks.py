"""MCP 任务组工具：list_my_tasks / claim / complete / reject / anticipate（5 个）。"""

from __future__ import annotations

from typing import Any

from flowork.api import ProcessEngine
from flowork.contrib.mcp.catalog import McpToolError, ToolSpec

__all__ = ["task_tools"]


def task_tools(engine: ProcessEngine) -> list[ToolSpec]:
    task = engine.task

    def list_my_tasks(args: dict[str, Any]) -> Any:
        assignee = args.get("assignee")
        if assignee is None:
            raise McpToolError("assignee 必填（裸 id）")
        rows = task.list_tasks(
            instance_id=args.get("instance_id"),
            assignee=str(assignee),
            candidate=args.get("candidate"),
            state=str(args.get("state") or "ready"),
        )
        return [row.model_dump(mode="json") for row in rows]

    def claim_task(args: dict[str, Any]) -> Any:
        return task.claim(str(args.get("task_id") or ""), str(args.get("user") or "")).model_dump(
            mode="json"
        )

    def complete_task(args: dict[str, Any]) -> Any:
        variables = args.get("variables")
        result = task.complete(
            str(args.get("task_id") or ""),
            str(args.get("actor") or "mcp"),
            comment=str(args.get("comment") or ""),
            variables=variables if isinstance(variables, dict) else None,
            idempotency_key=args.get("idempotency_key"),
        )
        return result.model_dump(mode="json")

    def reject_task(args: dict[str, Any]) -> Any:
        result = task.reject(
            str(args.get("task_id") or ""),
            str(args.get("actor") or "mcp"),
            comment=str(args.get("comment") or ""),
            target=args.get("target"),
        )
        return result.model_dump(mode="json")

    def anticipate_task(args: dict[str, Any]) -> Any:
        return task.anticipate(
            str(args.get("task_id") or ""), outcome=str(args.get("outcome") or "agree")
        ).model_dump(mode="json")

    return [
        ToolSpec(
            name="list_my_tasks",
            description="我的待办（assignee 必填；candidate=我可认领的候选池）",
            input_schema={
                "type": "object",
                "properties": {
                    "assignee": {"type": "string"},
                    "state": {"type": "string"},
                    "candidate": {"type": "string"},
                    "instance_id": {"type": "string"},
                },
                "required": ["assignee"],
            },
            handler=list_my_tasks,
        ),
        ToolSpec(
            name="claim_task",
            description="候选人认领任务（assignee 置为本人）",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "user": {"type": "string"},
                },
                "required": ["task_id", "user"],
            },
            handler=claim_task,
        ),
        ToolSpec(
            name="complete_task",
            description="同意提交（幂等；返回行动提示 next_tasks）",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "actor": {"type": "string"},
                    "comment": {"type": "string"},
                    "variables": {"type": "object"},
                    "idempotency_key": {"type": "string"},
                },
                "required": ["task_id"],
            },
            handler=complete_task,
        ),
        ToolSpec(
            name="reject_task",
            description="驳回回退（显式目标 > reject_target > 来源节点）",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "actor": {"type": "string"},
                    "comment": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["task_id"],
            },
            handler=reject_task,
        ),
        ToolSpec(
            name="anticipate_task",
            description="只读预判「照此提交后节点是否出、实例是否完」（不落库）",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "outcome": {"type": "string", "enum": ["agree", "reject"]},
                },
                "required": ["task_id"],
            },
            handler=anticipate_task,
        ),
    ]
