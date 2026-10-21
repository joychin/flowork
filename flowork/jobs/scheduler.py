"""Job 调度：超时策略执行 + 三级状态（scheduled → done / dead-letter）+ 退避重试。

用法::

    scheduler = engine.jobs          # JobScheduler(engine)
    scheduler.tick()                 # 手动推进（测试/外部定时器均可）
    scheduler.start() / stop()       # APScheduler 后台轮询（可选）

时钟经 EngineConfig.now 注入，超时测试可完全确定性。

多副本安全：每个 Job 先经 CAS（``state='scheduled' → 'running'``，条件 UPDATE）
原子认领，认领失败即跳过——两个副本同时 tick 同一 Job 只会执行一次。
认领、策略执行与结果落库在同一事务内：崩溃整体回滚（Job 回到 scheduled，
由下次 tick 重试），不存在滞留 running 的中间态。
策略执行面拆至 strategies.py（Mixin 组合，入口类不变）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from flowork.engine.kernel import EngineExecutionError
from flowork.engine.ops import CommandContext
from flowork.events.domain import DomainEvent
from flowork.jobs.strategies import DeadlineStrategiesMixin
from flowork.persistence.orm import JobRow, ProcessInstanceRow

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["JobScheduler"]

logger = logging.getLogger("flowork.jobs")

_BACKOFF_BASE_SECONDS = 30


class JobScheduler(DeadlineStrategiesMixin):
    """到期 Job 的执行器：超时策略（notify / auto_approve / auto_reject）。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine
        self._scheduler = None

    # —— 推进 ——

    def tick(self, *, now: datetime | None = None) -> int:
        """执行全部到期 Job，返回实际执行数量（含进入 dead-letter 的）。"""
        moment = now or self._engine.config.now()

        def _collect(ctx: CommandContext) -> list[str]:
            # 暂停与挂起期 timer 冻结（挂起
            # 实例的时限曾被照常认领、被「skip」结案永久吞掉——冻结至故障解决，
            # 恢复后到点仍触发）
            frozen_ids = select(ProcessInstanceRow.id).where(
                ProcessInstanceRow.state.in_(("paused", "suspended"))
            )
            stmt = (
                select(JobRow.id)
                .where(
                    JobRow.state == "scheduled",
                    JobRow.run_at <= moment,
                    JobRow.job_kind != "worker",  # worker 类由外部领取-汇报面执行
                    JobRow.instance_id.not_in(frozen_ids),
                )
                .order_by(JobRow.run_at)
            )
            return list(ctx.session.scalars(stmt))

        due: list[str] = self._engine.execute(_collect)
        return sum(1 for job_id in due if self._run_one(job_id, moment))

    def _run_one(self, job_id: str, moment: datetime) -> bool:
        """认领并执行单个 Job。返回是否由本次调用执行（认领失败为 False）。"""

        def _command(ctx: CommandContext) -> bool:
            # CAS 认领：条件 UPDATE + RETURNING，认领失败（0 行）即返回
            claimed_id = ctx.session.execute(
                update(JobRow)
                .where(JobRow.id == job_id, JobRow.state == "scheduled")
                .values(state="running")
                .returning(JobRow.id)
            ).scalar_one_or_none()
            if claimed_id is None:
                return False  # 已被并发 tick 认领，或状态已变
            job = ctx.session.get(JobRow, job_id)
            assert job is not None
            try:
                # savepoint：策略失败只回滚流程侧变更，认领与重试标记保留
                with ctx.session.begin_nested():
                    self._apply_strategy(ctx, job)
            except EngineExecutionError as exc:
                # 引擎错误：与命令管线同口径，同事务记 Incident 并挂起实例
                self._engine.runtime._record_incident_in_ctx(ctx, [job.instance_id], exc)
                self._mark_failure(ctx, job, exc, moment)
            except Exception as exc:
                self._mark_failure(ctx, job, exc, moment)
            else:
                if job.state == "scheduled":
                    # 策略显式回置 scheduled＝冻结期/咨询期推迟不消费——保持 scheduled，
                    # 由解冻/咨询交回后的 tick 触发，不记 finished_at
                    return True
                job.state = "done"
                job.result_payload = {
                    **(job.result_payload or {}),
                    "finished_at": moment.isoformat(),
                }
            return True

        return bool(self._engine.execute(_command))

    def run_virtual(self, job_id: str, moment: datetime) -> bool:
        """执行单个 virtual Job（模拟域虚拟时钟）：策略与真实执行同源。

        认领条件 ``state='virtual'``（tick 只认 scheduled——模拟 Job 对真实
        调度器不可见）；冻结/咨询推迟被策略回置 scheduled 后**拉回 virtual**
        （不落真实域）；策略失败上抛给模拟驱动器（诚实停点，不重试不死信）。
        """
        from sqlalchemy import update

        def _command(ctx: CommandContext) -> bool:
            claimed_id = ctx.session.execute(
                update(JobRow)
                .where(JobRow.id == job_id, JobRow.state == "virtual")
                .values(state="running")
                .returning(JobRow.id)
            ).scalar_one_or_none()
            if claimed_id is None:
                return False
            job = ctx.session.get(JobRow, job_id)
            assert job is not None
            try:
                with ctx.session.begin_nested():
                    self._apply_strategy(ctx, job)
            except Exception as exc:
                # 模拟域：策略异常不得穿出驱动循环（整次模拟会丢报告）——
                # Job 结案留痕（**独立事务**持久化：本命令随后上抛 FloworkError
                # 会整体回滚，同事务写 done 会被一起滚掉——2026-10-21 内核
                # review P1-6 副刀：修前「留痕」实际不落库，Job 永远留在
                # virtual 且无任何失败证据），错误归一由驱动器记 notes
                with self._engine.session_factory() as close_session, close_session.begin():
                    close_session.execute(
                        update(JobRow)
                        .where(JobRow.id == job_id)
                        .values(
                            state="done",
                            last_error=str(exc),
                            result_payload={
                                **(job.result_payload or {}),
                                "skipped": str(exc),
                            },
                        )
                    )
                from flowork.errors import FloworkError

                raise FloworkError(f"虚拟时钟策略执行失败（job={job.id}）：{exc}") from exc
            if job.state == "scheduled":
                # 策略显式回置 scheduled＝冻结/咨询推迟不消费——留在虚拟域，
                # 由后续推钟再次兑现
                job.state = "virtual"
                return True
            job.state = "done"
            job.result_payload = {
                **(job.result_payload or {}),
                "finished_at": moment.isoformat(),
            }
            return True

        return bool(self._engine.execute(_command))

    def _mark_failure(
        self, ctx: CommandContext, job: JobRow, exc: Exception, moment: datetime
    ) -> None:
        """失败落账：退避重试或进入 dead-letter（含死信事件）。"""
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
            job.state = "scheduled"
            job.run_at = moment + timedelta(seconds=backoff)

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
        """启动后台轮询（APScheduler）。测试用 tick() 即可，无需 start()。"""
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
