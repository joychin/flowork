"""任务超时排程：按节点 deadlines 列表创建 timer Job（v2.2 spec §5.1）。

首条到期时刻物化进 task.due_at（due_before 过滤与排序）；
auto_approve/auto_reject 终结等待后剩余 Job 因任务已完自然跳过。
转办/超时改派克隆任务行时，未触发档位随等待实体携带（carry_deadline_jobs）。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from flowork.definitions.node import effective_deadlines

if TYPE_CHECKING:
    from flowork.definitions.builtin import UserTaskSpec
    from flowork.engine.context import RuntimeNodeContext
    from flowork.persistence.orm import TaskRow

__all__ = ["carry_deadline_jobs", "schedule_deadline_jobs"]


def carry_deadline_jobs(session: Any, old: TaskRow, new: TaskRow) -> None:
    """把绑定旧任务的未触发 timer Job 重绑到新任务（run_at 不变，原 Job 置 done）。

    档位属于节点等待而非任务行——否则改派克隆体丢失剩余档
    （如 reassign@10 之后的 auto_approve@15 永不触发，SLA 出现缺口）。
    """
    from sqlalchemy import select

    from flowork.persistence.orm import JobRow

    jobs = session.scalars(
        select(JobRow).where(
            JobRow.job_type == "timer",
            JobRow.state == "scheduled",
            JobRow.instance_id == old.instance_id,
        )
    )
    carried = False
    for job in jobs:
        if job.payload.get("task_id") != old.id:
            continue
        payload = dict(job.payload)
        payload["task_id"] = new.id
        session.add(
            JobRow(
                id=uuid.uuid4().hex,
                job_type="timer",
                state="scheduled",
                run_at=job.run_at,
                instance_id=job.instance_id,
                payload=payload,
            )
        )
        job.state = "done"
        job.result_payload = {**(job.result_payload or {}), "carried_to": new.id}
        carried = True
    if carried:
        new.due_at = old.due_at


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
                    **({"escalate_to": deadline.escalate_to} if deadline.escalate_to else {}),
                },
            )
        )
    task = ctx.session.get(TaskRow, task_id)
    if task is not None:
        task.due_at = now + timedelta(minutes=deadlines[0].after_minutes)
