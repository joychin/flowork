"""虚拟时钟兑现：fire_one_due_job（自 simulation_controls 拆出守 300 行红线）。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["fire_one_due_job"]


def fire_one_due_job(
    engine: ProcessEngine, instance_id: str, virtual_now: datetime, notes: list[str]
) -> bool:
    """兑现一个到期的 virtual Job（run_at 最早优先，策略与真实执行同源）。

    证据进 notes（事件面对模拟静默——推钟兑现的可发现性走这里与已办行）。
    到期判定带 5 秒容差：run_at 与虚拟基线是同命令内两次 config.now()，
    真实墙钟下有毫秒级偏移（推钟粒度是分钟，容差诚实无害）。
    """
    from datetime import timedelta

    from sqlalchemy import select

    def _collect(ctx: Any) -> str | None:
        from flowork.engine.simulation_runner import tree_instance_ids
        from flowork.persistence.orm import JobRow

        job_id: str | None = ctx.session.scalars(
            select(JobRow.id)
            .where(
                JobRow.state == "virtual",
                JobRow.run_at <= virtual_now + timedelta(seconds=5),
                JobRow.instance_id.in_(tree_instance_ids(ctx, instance_id)),
            )
            .order_by(JobRow.run_at, JobRow.id)
        ).first()
        return job_id

    job_id = engine.execute(_collect)
    if job_id is None:
        return False
    engine.jobs.run_virtual(job_id, virtual_now)

    def _read(ctx: Any) -> Any:
        from flowork.persistence.orm import JobRow

        return ctx.session.get(JobRow, job_id)

    job = engine.execute(_read)
    payload = job.payload if job else {}
    result_payload = (job.result_payload or {}) if job else {}
    deferred = result_payload.get("deferred")
    if job is not None and job.state == "virtual" and deferred:
        # 策略推迟（咨询未完/实例冻结）拉回 virtual 且 run_at 不变——
        # 对驱动循环这不是「已兑现」（修前返回
        # True 触发 continue，同一 Job 反复入选直到步数上限，咨询待办永远
        # 排不上）。返回 False 让决策/机器人处理继续推进、解除推迟。
        notes.append(
            f"虚拟时钟推迟 {payload.get('kind', 'task_deadline')}"
            f"@{payload.get('activity_code', '-')}（{deferred}，等人工/推钟后再兑）"
        )
        return False
    line = (
        f"虚拟时钟兑现 {payload.get('kind', 'task_deadline')}"
        f"@{payload.get('activity_code', '-')} strategy={payload.get('strategy', '-')}"
    )
    skipped = result_payload.get("skipped")
    if skipped:
        line += f"（跳过：{skipped}）"
    notes.append(line)
    return True
