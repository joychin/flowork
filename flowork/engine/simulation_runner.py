"""模拟驱动器：把 simulated 实例驱动到停点（机器人缺省＝全通过；控制面见 simulation_controls）。

基座（批 1a 任务行化）：模拟实例建真实任务行，办理全部经真实任务动作面——
会签/协办/候选语义与真实运行同源。驱动优先级：到期 virtual Job（虚拟时钟
兑现）→ 决策（按序扮演，下一决策暂无对应待办且时钟未耗尽时先推钟）→
机器人（全通过；给了 decisions 键即禁用）→ 等待假设 → 停点。

停点＝终态 / wait 冻结（无假设且时钟耗尽）/ 待决策 / 推进失败（错误进
报告 notes 不上抛）。驱动范围＝实例全树（子实例递归传播 simulated）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.engine.simulation_clock import fire_one_due_job
from flowork.engine.simulation_controls import (
    SimulationControls,
    instance_started_at,
    step_virtual_clock,
)
from flowork.errors import FloworkError, TaskNotAvailableError

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.tasks.models import TaskInfo

__all__ = ["SIMULATION_ACTOR", "drive_simulation", "resolve_wait", "tree_instance_ids"]

SIMULATION_ACTOR = "simulator"
_MAX_STEPS = 500


def drive_simulation(
    engine: ProcessEngine, instance_id: str, controls: SimulationControls | None = None
) -> tuple[list[str], int]:
    """驱动到停点；返回（说明性注记，累计推钟分钟）——进报告 notes/advanced_minutes。"""
    controls = controls or SimulationControls()
    notes: list[str] = []
    queue = list(controls.decisions)
    clock_steps = list(controls.clock)
    virtual_now = instance_started_at(engine, instance_id)
    advanced = 0
    for _ in range(_MAX_STEPS):
        if _instance_terminal(engine, instance_id):
            return notes, advanced  # 终态即收束：不再消费剩余时钟/决策
        try:
            fired = fire_one_due_job(engine, instance_id, virtual_now, notes)
        except FloworkError as exc:
            notes.append(f"虚拟时钟兑现失败：{exc}")
            return notes, advanced
        if fired:
            continue
        pending = _pending_tasks(engine, instance_id)
        if pending and controls.decisions_given and queue:
            step = queue[0]
            if _locate(engine, instance_id, str(step["node"]), step.get("by")) is not None:
                try:
                    _apply_decision(engine, instance_id, step)
                except FloworkError as exc:
                    notes.append(f"决策 {step.get('node')}/{step.get('action')} 失败：{exc}")
                    return notes, advanced
                queue.pop(0)
            elif clock_steps:
                virtual_now, advanced = step_virtual_clock(
                    engine, instance_id, clock_steps.pop(0), virtual_now, advanced, notes
                )
            else:
                notes.append(
                    f"待决策：决策 {step.get('node')}/{step.get('action')}"
                    "暂无对应待办且时钟已耗尽（机器人已被决策清单禁用）"
                )
                return notes, advanced
            continue
        if pending and controls.decisions_given:
            if clock_steps:
                virtual_now, advanced = step_virtual_clock(
                    engine, instance_id, clock_steps.pop(0), virtual_now, advanced, notes
                )
                continue
            nodes = "、".join(sorted({f"{item[1]}@{item[0][:8]}" for item in pending}))
            notes.append(f"待决策：控制面未提供决策的待办停在 {nodes}（机器人已被决策清单禁用）")
            return notes, advanced
        if pending:  # 机器人全通过（基座语义）
            # 协办/咨询先办：主任务在咨询未完时被拦（软挂起），顺序错了会假卡
            pending.sort(key=lambda item: 0 if item[1] in ("consult", "assist") else 1)
            for task_id, task_type, assignee in pending:
                try:
                    _robot_act(engine, task_id, task_type, assignee)
                except FloworkError as exc:
                    notes.append(f"模拟停在待办 {task_id}：{exc}")
                    return notes, advanced
            continue
        try:
            resolved = resolve_wait(engine, instance_id, controls.waits)
        except FloworkError as exc:
            notes.append(f"等待兑现失败：{exc}")
            return notes, advanced
        if resolved:
            continue
        if clock_steps:
            virtual_now, advanced = step_virtual_clock(
                engine, instance_id, clock_steps.pop(0), virtual_now, advanced, notes
            )
            continue
        return notes, advanced  # 终态，或无假设的冻结等待
    notes.append(f"模拟步数超过 {_MAX_STEPS} 上限，已停止（图中可能存在循环）")
    return notes, advanced


def _instance_terminal(engine: ProcessEngine, instance_id: str) -> bool:
    """实例是否已落终态（终态即收束：剩余时钟/决策不再消费）。"""

    def _read(ctx: Any) -> bool:
        from flowork.persistence.orm import ProcessInstanceRow
        from flowork.runtime.lifecycle import TERMINAL_INSTANCE_STATES

        row = ctx.session.get(ProcessInstanceRow, instance_id)
        return row is not None and row.state in TERMINAL_INSTANCE_STATES

    return engine.execute(_read)


def _pending_tasks(engine: ProcessEngine, instance_id: str) -> list[tuple[str, str, str]]:
    """实例全树的待办（task_id, task_type, assignee）。"""

    def _query(ctx: Any) -> list[tuple[str, str, str]]:
        from sqlalchemy import select

        from flowork.persistence.orm import TaskRow

        tree = tree_instance_ids(ctx, instance_id)
        rows = ctx.session.scalars(
            select(TaskRow)
            .where(TaskRow.instance_id.in_(tree), TaskRow.state == "ready")
            # 排序钉死（2026-10-21 内核 review P2）：PG 下无 ORDER BY 的返回
            # 顺序不定——报告 visited/机器人动作序要 CI 级确定
            .order_by(TaskRow.created_at, TaskRow.id)
        )
        return [(row.id, row.task_type, row.assignee) for row in rows]

    return engine.execute(_query)


def tree_instance_ids(ctx: Any, instance_id: str) -> list[str]:
    """本实例 + 全部后代（子流程可嵌套，逐层收）。"""
    from sqlalchemy import select

    from flowork.persistence.orm import ProcessInstanceRow

    ids = [instance_id]
    frontier = [instance_id]
    while frontier:
        frontier = list(
            ctx.session.scalars(
                select(ProcessInstanceRow.id)
                .where(ProcessInstanceRow.parent_id.in_(frontier))
                .order_by(ProcessInstanceRow.id)  # PG 下兄弟序确定（同上）
            )
        )
        ids.extend(frontier)
    return ids


def _locate(engine: ProcessEngine, instance_id: str, node: str, by: Any) -> TaskInfo | None:
    """决策定位（全树寻址，只读）：找得到返回任务，找不到返回 None。"""
    from flowork.tasks.action_steps import find_pending_task

    def _read(ctx: Any) -> list[str]:
        return tree_instance_ids(ctx, instance_id)

    ambiguous = ""
    for candidate_id in engine.execute(_read):
        try:
            return find_pending_task(engine, candidate_id, node=node, by=by)
        except TaskNotAvailableError as exc:
            # 多条待办需 by 指定的歧义不是「暂无」（2026-10-21 review P2：
            # 修前吞成「暂无对应待办」诊断跑偏）——向上透传原话
            if "条待办" in str(exc):
                ambiguous = str(exc)
    if ambiguous:
        raise TaskNotAvailableError(ambiguous)
    return None


def _apply_decision(engine: ProcessEngine, instance_id: str, step: dict[str, Any]) -> None:
    """按序应用一条决策（真实动作面；子实例节点一并寻址）。"""
    from flowork.tasks.action_steps import dispatch_task_action

    task = _locate(engine, instance_id, str(step["node"]), step.get("by"))
    if task is None:  # pragma: no cover - 驱动循环已先判定可定位
        raise TaskNotAvailableError(f"节点 {step['node']} 没有就绪任务")
    actor = str(step.get("by") or task.assignee or SIMULATION_ACTOR)
    dispatch_task_action(engine, task.id, str(step["action"]), actor=actor, step=step)


def resolve_wait(engine: ProcessEngine, instance_id: str, waits: dict[str, dict[str, Any]]) -> bool:
    """有假设控制的冻结等待节点：按 matched/elapsed 兑现（含载荷映射）。"""
    if not waits:
        return False

    def _find(ctx: Any) -> list[tuple[str, str, str]]:
        from sqlalchemy import select

        from flowork.persistence.orm import ProcessInstanceRow, TokenRow

        kernel = engine.kernel_for(ctx)
        found: list[tuple[str, str, str]] = []
        for inst_id in tree_instance_ids(ctx, instance_id):
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
                # 等待类节点判别走行为标志（kind 知识住行为——message_flow 同款）
                if (
                    behavior is not None
                    and getattr(behavior, "advances_on_message", False)
                    and token.activity_code in waits
                ):
                    found.append((inst_id, token.id, token.activity_code))
        return found

    entries = engine.execute(_find)
    if not entries:
        return False

    def _apply(ctx: Any) -> None:
        import json as _json

        from flowork.engine.message_flow import _wait_mappings_of
        from flowork.engine.message_query import resolve_payload_write
        from flowork.persistence.orm import ProcessInstanceRow, TokenRow
        from flowork.tasks.wait import finish_wait

        kernel = engine.kernel_for(ctx)
        inst_id, token_id, node_code = entries[0]
        instance = ctx.session.get(ProcessInstanceRow, inst_id)
        token = ctx.session.get(TokenRow, token_id)
        assert instance is not None and token is not None
        ctx.touch(instance)
        control = waits[node_code]
        node = kernel.definition_of(ctx.session, instance).node(node_code)
        assert node is not None
        if control["result"] == "matched" and control.get("payload") is not None:
            # 载荷与消息路径同语义：output_mappings（缺省全量合并）+ 契约边界
            from flowork.definitions.contract import validate_input_contract

            write = resolve_payload_write(
                engine.config,
                dict(control["payload"]),
                _wait_mappings_of(node),
                node_code,
                instance=instance,
            )
            merged = dict(instance.variables)
            merged.update(write)
            validate_input_contract(
                (_json.loads(instance.definition_snapshot) or {}).get("input"),
                merged,
                boundary="node_output",
            )
            instance.variables.update(write)
        message_name = None
        if control["result"] == "matched":
            from flowork.definitions.wait_node import wait_fields

            message, _timeout = wait_fields(node)
            message_name = message.name if message is not None else None
        finish_wait(
            kernel,
            ctx.session,
            instance,
            token,
            result=control["result"],
            message_name=message_name,
        )

    engine.execute(_apply)
    return True


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
