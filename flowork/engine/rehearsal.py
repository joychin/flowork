"""排练：simulated 实例的交互驱动（人点办理，一步一单）。

与整图模拟同一基座（任务行化）：simulated 实例、真实任务行、真实动作面、
隔离语义全同；差别只在**驱动形态**——机器人在 simulate 里一次跑完，排练
把每一步交给操作者（web 排练面板）。状态快照就是「步单」的数据源：待办
（含服务端算好的 available_actions）＋已办行＋路由决策＋变量现值与血缘；
事件面对模拟静默，故步单不含事件（四项留痕面替代）。

场景脚本（scenario）复用同一批机件：按序定位待办→分派动作→推钟兑现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError, InstanceNotFoundError

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = [
    "perform_rehearsal_step",
    "rehearsal_state",
    "start_rehearsal",
    "tree_task_infos",
]

REHEARSAL_ACTOR = "rehearsal"


def start_rehearsal(
    engine: ProcessEngine,
    code: str,
    *,
    variables: dict[str, Any] | None = None,
    spec: dict[str, Any] | None = None,
    major: int | None = None,
    revision: int | None = None,
    start_user: str = REHEARSAL_ACTOR,
    controls: dict[str, Any] | None = None,
) -> str:
    """启动排练实例（不驱动）：返回实例 id。桩/等待假设可预置。"""
    instance_id, _controls = engine.runtime.start_simulation(
        code,
        variables=variables,
        start_user=start_user,
        major=major,
        revision=revision,
        spec=spec,
        controls=controls,
    )
    return instance_id


def perform_rehearsal_step(engine: ProcessEngine, instance_id: str, step: dict[str, Any]) -> None:
    """执行一个排练步：任务动作（十词表）或等待节点兑现。

    步种类用分派表（``task`` 缺省 / ``wait``）：等待步 ``{node,
    result: matched|elapsed, payload?}`` 把冻结的等待节点按假设兑现；
    任务步 ``{task_id, action, by?, comment?, variables?, target?, to?,
    users?}``——候选池任务（未认领）自动以操作者认领后办理（排练的操作者
    就是办事人，认领是流程细节不是演练关卡）。
    """
    kind = str(step.get("kind") or "task")
    handler = _STEP_HANDLERS.get(kind)
    if handler is None:
        raise FloworkError(f"未知的排练步类型：{kind}（可选 {sorted(_STEP_HANDLERS)}）")
    _require_simulated(engine, instance_id)
    handler(engine, instance_id, step)


def _require_simulated(engine: ProcessEngine, instance_id: str) -> None:
    """排练只驱动模拟域实例：真实实例的动作面不对排练端点开放（越权面）。"""

    def _read(ctx: Any) -> bool:
        from flowork.persistence.orm import ProcessInstanceRow

        row = ctx.session.get(ProcessInstanceRow, instance_id)
        return row is not None and bool(row.simulated)

    if not engine.execute(_read):
        raise InstanceNotFoundError(f"排练实例不存在或不是模拟实例：{instance_id}")


def _wait_step(engine: ProcessEngine, instance_id: str, step: dict[str, Any]) -> None:
    """等待步：把冻结的等待节点按假设兑现（载荷走 output_mappings 同语义）。"""
    node = str(step.get("node") or "")
    result = step.get("result")
    if not node or result not in ("matched", "elapsed"):
        raise FloworkError("等待步须给 node 与 result（matched/elapsed）")
    from flowork.engine.simulation_runner import resolve_wait

    if not resolve_wait(
        engine,
        instance_id,
        {node: {"result": result, "payload": step.get("payload") or None}},
    ):
        raise FloworkError(f"等待节点 {node} 当前不在等待中")


def _task_step(engine: ProcessEngine, instance_id: str, step: dict[str, Any]) -> None:
    """任务步：定位→（候选池自动认领）→真实动作面分派。"""
    from flowork.tasks.action_steps import dispatch_task_action

    task_id = str(step.get("task_id") or "")
    action = str(step.get("action") or "")
    if not task_id or not action:
        raise FloworkError("任务步须给 task_id 与 action")
    task = engine.task.get_task(task_id)
    if task is None or task.state != "ready":
        raise FloworkError(f"任务不可办理（不存在或已处理）：{task_id}")
    _require_in_tree(engine, instance_id, task.instance_id)
    actor = str(step.get("by") or task.assignee or REHEARSAL_ACTOR)
    if task.assignee == "" and action not in ("claim", "unclaim"):
        engine.task.claim(task_id, actor)
    dispatch_task_action(engine, task_id, action, actor=actor, step={**step, "by": actor})


_STEP_HANDLERS = {
    "task": _task_step,
    "wait": _wait_step,
}


def rehearsal_state(engine: ProcessEngine, instance_id: str) -> dict[str, Any]:
    """步单快照：实例/待办/已办/路由/变量与血缘/等待点（全树）。"""
    try:
        instance = engine.runtime.get_instance(instance_id)
    except FloworkError as exc:
        raise InstanceNotFoundError(f"排练实例不存在：{instance_id}") from exc
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


def _require_in_tree(engine: ProcessEngine, root: str, instance_id: str) -> None:
    if instance_id not in _tree_ids(engine, root):
        raise FloworkError(f"任务不属于该排练实例：{instance_id}")


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
