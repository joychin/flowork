"""auto_pass 归档统一咽喉。

四处 auto_pass 归档（参与者策略命中/会签出口达成/节点已否决/管理员强制完成）
此前零事件——消费者无从知道「谁被自动通过」；且策略命中者是「建了即归档」的
幻影任务，task_created 照发（待办通知误报，由 create_task(emit_event=False)
在调用侧抑制）。本咽喉统一：归档（outcome=auto_pass）+ ``task_auto_passed``
事件，``reason`` 区分四路：strategy / exit_reached / node_rejected / admin_finish。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.events.domain import DomainEvent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.engine.kernel import Kernel
    from flowork.persistence.orm import TaskRow

__all__ = ["archive_auto_pass"]


def archive_auto_pass(
    kernel: Kernel,
    session: Session,
    task: TaskRow,
    *,
    reason: str,
    comment: str = "",
    actor: str = "system",
    cause_token_id: str | None = None,
) -> None:
    """归档为 auto_pass 并发射 task_auto_passed（载荷与 task 族统一带定位字段）。"""
    kernel.archive_task(
        session,
        task,
        outcome="auto_pass",
        comment=comment,
        actor=actor,
        cause_token_id=cause_token_id,
    )
    kernel.record_event(
        DomainEvent.TASK_AUTO_PASSED,
        {
            "task_id": task.id,
            "activity_code": task.activity_code,
            "task_type": task.task_type,
            "assignee": task.assignee,
            "reason": reason,
            # actor 入载荷：订阅方可区分
            # 策略代投（system）与管理员强制完成（admin_finish）——历史行本就带
            "actor": actor,
        },
        task.instance_id,
    )
