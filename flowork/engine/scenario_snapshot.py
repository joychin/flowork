"""场景快照：一次 simulated 实例运行的留痕面汇总（断言与覆盖照明的数据源）。

场景断言（final_state/visited/task/variable/pending）和节点/边覆盖都要一份
「这次跑完长什么样」的快照——待办、已办行、路由决策、变量现值与血缘、等待点，
全树合并。它只读不驱动，与运行器解耦。

（历史上的「排练」面板曾用同一份快照做人点驱动的步单数据源；那个交互面已移除，
快照本身是场景断言的一部分，保留在此。）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError, InstanceNotFoundError

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["scenario_state", "tree_task_infos"]


def scenario_state(engine: ProcessEngine, instance_id: str) -> dict[str, Any]:
    """快照：实例/待办/已办/路由/变量与血缘/等待点（全树）。"""
    try:
        instance = engine.runtime.get_instance(instance_id)
    except FloworkError as exc:
        raise InstanceNotFoundError(f"实例不存在：{instance_id}") from exc
    tree = _tree_ids(engine, instance_id)
    tasks = tree_task_infos(engine, tree)
    return {
        "instance": instance.model_dump(mode="json"),
        "tasks": [task.model_dump(mode="json") for task in tasks],
        "task_history": _task_history(engine, tree),
        "routes": _routes(engine, tree),
        "variables": engine.runtime.variables_of(instance_id),
        "variable_history": [
            _change_payload(change)
            for change in engine.runtime.variable_history(instance_id, limit=200, offset=0)
        ],
        "waiting": _waiting_tokens(engine, tree),
    }


def tree_task_infos(engine: ProcessEngine, tree: list[str]) -> list[Any]:
    """全树就绪待办（list_tasks 单实例定向，逐实例合并——树规模小）。"""
    tasks = []
    for inst_id in tree:
        tasks.extend(engine.task.list_tasks(instance_id=inst_id))
    return tasks


def _tree_ids(engine: ProcessEngine, instance_id: str) -> list[str]:
    from flowork.engine.simulation_runner import tree_instance_ids

    def _read(ctx: Any) -> list[str]:
        return tree_instance_ids(ctx, instance_id)

    return engine.execute(_read)


def _task_history(engine: ProcessEngine, tree: list[str]) -> list[dict[str, Any]]:
    def _read(ctx: Any) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from flowork.persistence.orm import TaskHistoryRow

        rows = ctx.session.scalars(
            select(TaskHistoryRow)
            .where(TaskHistoryRow.instance_id.in_(tree))
            .order_by(TaskHistoryRow.created_at, TaskHistoryRow.id)
        )
        return [
            {
                "node": row.activity_code,
                "assignee": row.assignee,
                "acted_by": row.acted_by,
                "outcome": row.outcome,
                "comment": row.comment,
                "created_at": row.created_at.isoformat(timespec="seconds"),
            }
            for row in rows
        ]

    return engine.execute(_read)


def _routes(engine: ProcessEngine, tree: list[str]) -> list[dict[str, Any]]:
    def _read(ctx: Any) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from flowork.persistence.orm_v2 import RouteDecisionRow

        rows = ctx.session.scalars(
            select(RouteDecisionRow)
            .where(RouteDecisionRow.instance_id.in_(tree))
            .order_by(RouteDecisionRow.id)
        )
        return [
            {
                "node": row.source_activity,
                "selected": row.selected,
                "edges": row.edges,
                "snapshot": row.snapshot,
            }
            for row in rows
        ]

    return engine.execute(_read)


def _waiting_tokens(engine: ProcessEngine, tree: list[str]) -> list[dict[str, Any]]:
    def _read(ctx: Any) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from flowork.persistence.orm import ProcessInstanceRow, TokenRow

        kernel = engine.kernel_for(ctx)
        found: list[dict[str, Any]] = []
        for inst_id in tree:
            instance = ctx.session.get(ProcessInstanceRow, inst_id)
            if instance is None or instance.state != "running":
                continue
            definition = kernel.definition_of(ctx.session, instance)
            tokens = ctx.session.scalars(
                select(TokenRow).where(
                    TokenRow.instance_id == inst_id, TokenRow.state == "unfinished"
                )
            )
            for token in tokens:
                node = definition.node(token.activity_code)
                behavior = kernel.behavior_of(node) if node is not None else None
                if behavior is not None and getattr(behavior, "advances_on_message", False):
                    found.append({"node": token.activity_code, "instance_id": inst_id})
        return found

    return engine.execute(_read)


def _change_payload(change: Any) -> dict[str, Any]:
    return {
        "name": change.name,
        "old": change.old_value,
        "new": change.new_value,
        "source": change.source,
        "acted_by": change.acted_by,
        "changed_at": change.created_at,
    }
