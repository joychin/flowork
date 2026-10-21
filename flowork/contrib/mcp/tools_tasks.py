"""MCP 任务组工具：list_my_tasks / claim / complete / reject / anticipate（5 个）。"""

from __future__ import annotations

from typing import Any

from flowork.api import ProcessEngine
from flowork.contrib.mcp.catalog import McpToolError, ToolSpec, page_window, require_actor

__all__ = ["task_tools"]


def task_tools(engine: ProcessEngine) -> list[ToolSpec]:
    task = engine.task

    task_states = ("ready", "completed", "cancelled", "")

    def list_my_tasks(args: dict[str, Any]) -> Any:
        assignee = args.get("assignee")
        if assignee is None:
            raise McpToolError("assignee 必填（裸 id）")
        state = args.get("state")
        state = "ready" if state is None else str(state)
        if state not in task_states:
            # 修前非法值（如大小写错）静默空列表——AI 误判「无待办」；
            # ""=查全部（与 REST 透传语义一致，修前被 or "ready" 静默改写）
            raise McpToolError(
                f"state 不合法：{state!r}（可选 ready/completed/cancelled；空串=全部）"
            )
        rows = task.list_tasks(
            instance_id=args.get("instance_id"),
            assignee=str(assignee),
            candidate=args.get("candidate"),
            state=state,
        )
        return page_window(args, [row.model_dump(mode="json") for row in rows])

    def claim_task(args: dict[str, Any]) -> Any:
        user = str(args.get("user") or "")
        if not user:
            raise McpToolError("user 必填（认领人裸 id）")
        return task.claim(str(args.get("task_id") or ""), user).model_dump(mode="json")

    def complete_task(args: dict[str, Any]) -> Any:
        variables = args.get("variables")
        if variables is not None and not isinstance(variables, dict):
            # 修前非 dict 静默丢弃为 None——任务照常办结、变量无声丢失
            raise McpToolError("variables 必须是 {name: value} 对象")
        result = task.complete(
            str(args.get("task_id") or ""),
            require_actor(args),
            comment=str(args.get("comment") or ""),
            variables=variables,
            idempotency_key=args.get("idempotency_key"),
        )
        return result.model_dump(mode="json")

    def reject_task(args: dict[str, Any]) -> Any:
        result = task.reject(
            str(args.get("task_id") or ""),
            require_actor(args),
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
                    "state": {
                        "type": "string",
                        "enum": ["ready", "completed", "cancelled", ""],
                        "description": "省略=ready；空串=全部",
                    },
                    "candidate": {"type": "string"},
                    "instance_id": {"type": "string"},
                    "limit": {"type": "integer", "description": "1~500，缺省 50"},
                    "offset": {"type": "integer", "description": "非负，缺省 0"},
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
                "required": ["task_id", "actor"],
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
                "required": ["task_id", "actor"],
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
