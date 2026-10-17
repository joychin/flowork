"""模拟驱动器：把 simulated 实例的待办按策略办完（机器人缺省＝全通过）。

基座（批 1a 任务行化）：模拟实例建真实任务行，机器人经**真实任务动作面**
办理（claim/complete/complete_assist/complete_consult）——办理、会签计数、
协办收口语义与真实运行完全同源，无第二套实现。停点＝终态 / wait 冻结 /
推进失败（取人失败等诚实卡点：错误进报告 notes 不上抛）。驱动范围＝
实例全树（子实例递归传播 simulated，一并办理）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["SIMULATION_ACTOR", "drive_simulation"]

SIMULATION_ACTOR = "simulator"
_MAX_STEPS = 500


def drive_simulation(engine: ProcessEngine, instance_id: str) -> list[str]:
    """机器人驱动到停点，返回说明性注记（进报告 notes）。"""
    notes: list[str] = []
    for _ in range(_MAX_STEPS):
        pending = _pending_tasks(engine, instance_id)
        if not pending:
            return notes
        # 协办/咨询先办：主任务在咨询未完时被拦（软挂起），顺序错了会假卡
        pending.sort(key=lambda item: 0 if item[1] in ("consult", "assist") else 1)
        for task_id, task_type, assignee in pending:
            try:
                _robot_act(engine, task_id, task_type, assignee)
            except FloworkError as exc:
                notes.append(f"模拟停在待办 {task_id}：{exc}")
                return notes
    notes.append(f"模拟步数超过 {_MAX_STEPS} 上限，已停止（图中可能存在循环）")
    return notes


def _pending_tasks(engine: ProcessEngine, instance_id: str) -> list[tuple[str, str, str]]:
    """实例全树的待办（task_id, task_type, assignee）。"""

    def _query(ctx: Any) -> list[tuple[str, str, str]]:
        from sqlalchemy import select

        from flowork.persistence.orm import TaskRow

        tree = _tree_ids(ctx, instance_id)
        rows = ctx.session.scalars(
            select(TaskRow).where(TaskRow.instance_id.in_(tree), TaskRow.state == "ready")
        )
        return [(row.id, row.task_type, row.assignee) for row in rows]

    return engine.execute(_query)


def _tree_ids(ctx: Any, instance_id: str) -> list[str]:
    """本实例 + 全部后代（子流程可嵌套，逐层收）。"""
    from sqlalchemy import select

    from flowork.persistence.orm import ProcessInstanceRow

    ids = [instance_id]
    frontier = [instance_id]
    while frontier:
        frontier = list(
            ctx.session.scalars(
                select(ProcessInstanceRow.id).where(ProcessInstanceRow.parent_id.in_(frontier))
            )
        )
        ids.extend(frontier)
    return ids


def _robot_act(engine: ProcessEngine, task_id: str, task_type: str, assignee: str) -> None:
    """按任务类型选办理动词（全部走真实动作面）。"""
    if task_type == "consult":
        engine.task.complete_consult(task_id, SIMULATION_ACTOR, comment="模拟自动通过")
        return
    if task_type == "assist":
        engine.task.complete_assist(task_id, SIMULATION_ACTOR, comment="模拟自动通过")
        return
    if assignee == "":
        engine.task.claim(task_id, SIMULATION_ACTOR)
    engine.task.complete(task_id, SIMULATION_ACTOR, comment="模拟自动通过")
