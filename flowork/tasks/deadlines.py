"""任务超时排程：按节点 deadlines 列表创建 timer Job（v2.2 spec §5.1）。

首条到期时刻物化进 task.due_at（due_before 过滤与排序）；
auto_approve/auto_reject 终结等待后剩余 Job 因任务已完自然跳过。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from flowork.definitions.node import effective_deadlines

if TYPE_CHECKING:
    from flowork.definitions.builtin import UserTaskSpec
    from flowork.engine.context import RuntimeNodeContext

__all__ = ["schedule_deadline_jobs"]


def schedule_deadline_jobs(ctx: RuntimeNodeContext, spec: UserTaskSpec, task_id: str) -> None:
    """deadlines 逐条建 timer Job（≤8 条，validator 把关）。"""
    from flowork.persistence.orm import JobRow, TaskRow

    deadlines = effective_deadlines(spec)
    if not deadlines:
        return
    now = ctx.kernel.config.now()
    for index, deadline in enumerate(deadlines):
        ctx.session.add(
            JobRow(
                id=uuid.uuid4().hex,
                job_type="timer",
                state="scheduled",
                run_at=now + timedelta(minutes=deadline.after_minutes),
                instance_id=ctx.instance.id,
                payload={
                    "task_id": task_id,
                    "activity_code": spec.code,
                    "kind": "task_deadline",
                    "strategy": deadline.action.value,
                    "after_minutes": deadline.after_minutes,
                    "deadline_index": index,
                    **({"reassign_to": deadline.reassign_to} if deadline.reassign_to else {}),
                },
            )
        )
    task = ctx.session.get(TaskRow, task_id)
    if task is not None:
        task.due_at = now + timedelta(minutes=deadlines[0].after_minutes)
