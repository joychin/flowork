"""Job 调度：超时策略执行 + 三级状态（scheduled → done / dead-letter）+ 退避重试。

用法::

    scheduler = engine.jobs          # JobScheduler(engine)
    scheduler.tick()                 # 手动推进（测试/外部定时器均可）
    scheduler.start() / stop()       # APScheduler 后台轮询（可选）

时钟经 EngineConfig.now 注入，超时测试可完全确定性。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from flowork.engine.ops import CommandContext
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import JobRow

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["JobScheduler"]

logger = logging.getLogger("flowork.jobs")

_BACKOFF_BASE_SECONDS = 30


class JobScheduler:
    """到期 Job 的执行器：超时策略（notify / auto_approve / auto_reject）。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine
        self._scheduler = None

    # —— 推进 ——

    def tick(self, *, now: datetime | None = None) -> int:
        """执行全部到期 Job，返回处理数量（含进入 dead-letter 的）。"""
        moment = now or self._engine.config.now()

        def _collect(ctx: CommandContext) -> list[JobRow]:
            stmt = (
                select(JobRow)
                .where(JobRow.state == "scheduled", JobRow.run_at <= moment)
                .order_by(JobRow.run_at)
            )
            return list(ctx.session.scalars(stmt))

        due: list[JobRow] = self._engine.execute(_collect)
        for job in due:
            self._run_one(job.id, moment)
        return len(due)

    def _run_one(self, job_id: str, moment: datetime) -> None:
        def _command(ctx: CommandContext) -> None:
            job = ctx.session.get(JobRow, job_id)
            if job is None or job.state != "scheduled":
                return
            try:
                self._apply_strategy(job)
            except Exception as exc:
                job.last_error = str(exc)
                job.retry_count += 1
                if job.retry_count >= job.max_retries:
                    job.state = "dead_letter"
                    kernel = self._engine.kernel_for(ctx)
                    kernel.record_event(
                        DomainEvent.JOB_DEAD_LETTERED,
                        {"job_id": job.id, "error": str(exc)},
                        job.instance_id,
                    )
                else:
                    backoff = _BACKOFF_BASE_SECONDS * (2 ** (job.retry_count - 1))
                    job.run_at = moment + timedelta(seconds=backoff)
                return
            job.state = "done"
            job.result_payload = {"finished_at": moment.isoformat()}

        self._engine.execute(_command)

    def _apply_strategy(self, job: JobRow) -> None:
        strategy = job.payload.get("strategy", "notify")
        task_id = job.payload.get("task_id")
        if strategy == "notify" or not task_id:

            def _notify(ctx: CommandContext) -> None:
                kernel = self._engine.kernel_for(ctx)
                kernel.record_event(
                    "timeout_warned",
                    {"job_id": job.id, "strategy": strategy},
                    job.instance_id,
                )

            self._engine.execute(_notify)
            return
        if strategy == "auto_approve":
            self._engine.task.complete(
                task_id, actor="system:timeout", comment="超时自动通过", as_agent=True
            )
        elif strategy == "auto_reject":
            self._engine.task.reject(
                task_id, actor="system:timeout", comment="超时自动驳回", as_agent=True
            )
        else:  # pragma: no cover - 配置兜底
            raise ValueError(f"未知超时策略：{strategy}")

    def requeue(self, job_id: str) -> None:
        """人工重派 dead-letter Job（重置为 scheduled，清零重试计数）。"""

        def _command(ctx: CommandContext) -> None:
            job = ctx.session.get(JobRow, job_id)
            if job is None or job.state != "dead_letter":
                raise ValueError(f"Job 不存在或不在 dead_letter 状态：{job_id}")
            job.state = "scheduled"
            job.retry_count = 0
            job.run_at = self._engine.config.now()

        self._engine.execute(_command)

    # —— 后台轮询（可选）——

    def start(self, interval_seconds: int = 30) -> None:
        """启动后台轮询（APScheduler）。测试用 tick() 即可，无需 start。"""
        from apscheduler.schedulers.background import BackgroundScheduler

        if self._scheduler is not None:
            return
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(self.tick, "interval", seconds=interval_seconds, id="flowork-jobs")
        scheduler.start()
        self._scheduler = scheduler

    def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
