"""演练/模拟共用的任务步骤机件（内核共享件）：定位待办 + 十动作分派。

词表与演练剧本/场景脚本同构（node/by/action + comment/variables/target/to/
users）；分派全部经 TaskService 真实动作面，无第二套实现。错误用
FloworkError 家族——CLI 演练台包 ScriptError，模拟驱动器落报告 notes。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError, TaskNotAvailableError

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.tasks.models import TaskInfo

__all__ = ["KNOWN_TASK_ACTIONS", "dispatch_task_action", "find_pending_task"]

KNOWN_TASK_ACTIONS = frozenset(
    {
        "complete",
        "reject",
        "transfer",
        "assist",
        "consult",
        "claim",
        "unclaim",
        "add_countersigner",
        "complete_assist",
        "complete_consult",
    }
)


def _same_person(left: str, right: str) -> bool:
    return left.removeprefix("user:") == right.removeprefix("user:")


def _users_of(step: Mapping[str, Any]) -> list[str]:
    users = step.get("users")
    if isinstance(users, list) and users:
        return [str(u) for u in users]
    if isinstance(users, str) and users.strip():  # 逗号串形态容忍
        return [part.strip() for part in users.split(",") if part.strip()]
    to = step.get("to")
    if isinstance(to, str) and to.strip():
        return [part.strip() for part in to.split(",") if part.strip()]
    return []


def find_pending_task(
    engine: ProcessEngine, instance_id: str, *, node: str, by: str | None = None
) -> TaskInfo:
    """按 node（必要时加 by 对人）在就绪待办中定位任务。

    节点多条待办而不给 ``by``＝会签等并行场景的任意选择（list_tasks 无
    排序，结果不可复现）——拒绝并点明办理人名单，不静默任取。候选池任务
    （未认领）的 by 对错交引擎认领守卫判定，此处不复制候选判定。
    """
    pending = engine.task.list_tasks(instance_id=instance_id)
    at_node = [t for t in pending if t.activity_code == node]
    if not at_node:
        have = "、".join(sorted({t.activity_code for t in pending})) or "（当前无就绪待办）"
        raise TaskNotAvailableError(f"节点 {node} 没有就绪任务；当前待办节点：{have}")
    if by:
        matched = [t for t in at_node if _same_person(t.assignee, by)]
        if matched:
            at_node = matched
        elif not any(t.assignee == "" for t in at_node):
            who = "、".join(sorted({t.assignee for t in at_node}))
            raise TaskNotAvailableError(f"节点 {node} 的待办办理人是 {who}，不是 {by}")
    elif len(at_node) > 1:
        who = "、".join(sorted({t.assignee or "（候选池）" for t in at_node}))
        raise TaskNotAvailableError(
            f"节点 {node} 有 {len(at_node)} 条待办（{who}），请在步骤里用 by 指定办理人"
        )
    return at_node[0]


def dispatch_task_action(
    engine: ProcessEngine,
    task_id: str,
    action: str,
    *,
    actor: str,
    step: Mapping[str, Any],
) -> None:
    """把一个步骤动作翻译成 TaskService 调用（动作表全集）。"""
    if action not in KNOWN_TASK_ACTIONS:
        raise FloworkError(f"未知动作：{action}（可选 {sorted(KNOWN_TASK_ACTIONS)}）")
    comment = str(step.get("comment") or "")
    task_svc = engine.task
    if action == "complete":
        task_svc.complete(task_id, actor, comment=comment, variables=step.get("variables"))
    elif action == "reject":
        task_svc.reject(
            task_id,
            actor,
            comment=comment,
            target=step.get("target"),  # 手动指定驳回落点（缺省走节点 reject_target）
            variables=step.get("variables"),
        )
    elif action == "transfer":
        if not step.get("to"):
            raise FloworkError("transfer 动作必须给 to（被转办人）")
        task_svc.transfer(task_id, str(step["to"]), actor, comment)
    elif action in ("assist", "consult", "add_countersigner"):
        users = _users_of(step)
        if not users:
            raise FloworkError(f"{action} 动作必须给 users（或 to，逗号分隔）")
        getattr(task_svc, action)(task_id, users, actor, comment)
    elif action == "claim":
        task_svc.claim(task_id, actor)
    elif action == "unclaim":
        task_svc.unclaim(task_id, actor)
    else:  # complete_assist / complete_consult
        getattr(task_svc, action)(task_id, actor, comment)
