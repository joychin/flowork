"""场景步执行：按序定位待办 → 分派真实动作面 / 兑现等待节点。

场景脚本的每一步就是「一次人做的动作」——这里把它译成引擎的真实动作
（任务十词表 / 等待假设兑现），与 simulate 的决策清单同一批机件。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError, InstanceNotFoundError

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["perform_step", "start_run"]

DEFAULT_ACTOR = "scenario"


def start_run(
    engine: ProcessEngine,
    code: str,
    *,
    variables: dict[str, Any] | None = None,
    spec: dict[str, Any] | None = None,
    major: int | None = None,
    revision: int | None = None,
    start_user: str = DEFAULT_ACTOR,
    controls: dict[str, Any] | None = None,
) -> str:
    """启动场景运行的模拟实例（不驱动）：返回实例 id。桩/等待假设可预置。"""
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


def perform_step(engine: ProcessEngine, instance_id: str, step: dict[str, Any]) -> None:
    """执行一个场景步：任务动作（十词表）或等待节点兑现。

    步种类用分派表（``task`` 缺省 / ``wait``）：等待步 ``{node,
    result: matched|elapsed, payload?}`` 把冻结的等待节点按假设兑现；
    任务步 ``{task_id, action, by?, comment?, variables?, target?, to?,
    users?}``——候选池任务（未认领）自动以脚本给出的操作者认领后办理。
    """
    kind = str(step.get("kind") or "task")
    handler = _STEP_HANDLERS.get(kind)
    if handler is None:
        raise FloworkError(f"未知的步类型：{kind}（可选 {sorted(_STEP_HANDLERS)}）")
    _require_simulated(engine, instance_id)
    handler(engine, instance_id, step)


def _require_simulated(engine: ProcessEngine, instance_id: str) -> None:
    """场景只驱动模拟域实例：真实实例的动作面不对场景端点开放（越权面）。"""

    def _read(ctx: Any) -> bool:
        from flowork.persistence.orm import ProcessInstanceRow

        row = ctx.session.get(ProcessInstanceRow, instance_id)
        return row is not None and bool(row.simulated)

    if not engine.execute(_read):
        raise InstanceNotFoundError(f"场景实例不存在或不是模拟实例：{instance_id}")


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
    actor = str(step.get("by") or task.assignee or DEFAULT_ACTOR)
    if task.assignee == "" and action not in ("claim", "unclaim"):
        engine.task.claim(task_id, actor)
    dispatch_task_action(engine, task_id, action, actor=actor, step={**step, "by": actor})


_STEP_HANDLERS = {
    "task": _task_step,
    "wait": _wait_step,
}


def _require_in_tree(engine: ProcessEngine, root: str, instance_id: str) -> None:
    from flowork.engine.simulation_runner import tree_instance_ids

    def _read(ctx: Any) -> list[str]:
        return tree_instance_ids(ctx, root)

    if instance_id not in engine.execute(_read):
        raise FloworkError(f"任务不属于该场景实例：{instance_id}")
