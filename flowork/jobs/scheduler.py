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
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from flowork.engine.kernel import EngineExecutionError
from flowork.engine.ops import CommandContext
from flowork.errors import TaskNotAvailableError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import JobRow, ProcessInstanceRow, TaskRow
from flowork.runtime.lifecycle import TokenState

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
        """执行全部到期 Job，返回实际执行数量（含进入 dead-letter 的）。"""
        moment = now or self._engine.config.now()

        def _collect(ctx: CommandContext) -> list[str]:
            paused_ids = select(ProcessInstanceRow.id).where(ProcessInstanceRow.state == "paused")
            stmt = (
                select(JobRow.id)
                .where(
                    JobRow.state == "scheduled",
                    JobRow.run_at <= moment,
                    JobRow.job_kind != "worker",  # worker 类由外部领取-汇报面执行（§5.2）
                    JobRow.instance_id.not_in(paused_ids),  # 暂停期 timer 冻结（spec §7）
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

    def _apply_strategy(self, ctx: CommandContext, job: JobRow) -> None:
        """执行到期策略（同事务）；已被人工处理则按"跳过"结案（不重试不死信）。"""
        kernel = self._engine.kernel_for(ctx)
        kind = job.payload.get("kind", "task_deadline")
        if kind == "instance_deadline":
            self._apply_instance_deadline(ctx, job, kernel)
            return
        strategy = job.payload.get("strategy", "notify")
        task_id = job.payload.get("task_id")
        task = ctx.session.get(TaskRow, task_id) if task_id else None
        if strategy == "notify":
            if task_id and (task is None or task.state != "ready"):
                # 等待已被终结（auto_approve/reject 先行）：预警不再触发（spec §5.1）
                job.result_payload = {
                    **(job.result_payload or {}),
                    "skipped": "task already finished",
                }
                return
            kernel.record_event(
                "timeout_warned",
                {"job_id": job.id, "strategy": strategy},
                job.instance_id,
            )
            return
        if not task_id:
            return
        if task is None or task.state != "ready":
            job.result_payload = {**(job.result_payload or {}), "skipped": "task already finished"}
            return
        try:
            if strategy in ("reassign", "escalate"):
                self._execute_handover(ctx, job, task, strategy)
            else:
                self._execute_strategy(ctx, strategy, task_id)
        except TaskNotAvailableError:
            job.result_payload = {**(job.result_payload or {}), "skipped": "task already finished"}

    def _apply_instance_deadline(self, ctx: CommandContext, job: JobRow, kernel: Any) -> None:
        """实例时限到点（v2.2 spec §7）：mark 打标继续跑 / cancel 作废 / terminate 终止。"""
        from flowork.persistence.orm import ProcessInstanceRow

        strategy = job.payload.get("strategy", "mark")
        instance = ctx.session.get(ProcessInstanceRow, job.instance_id)
        if instance is None or instance.state in ("completed", "cancelled", "terminated"):
            job.result_payload = {**(job.result_payload or {}), "skipped": "instance finished"}
            return  # 实例已完成则静默不触发
        ctx.touch(instance)
        kernel.record_event(
            DomainEvent.INSTANCE_DEADLINE_REACHED,
            {
                "strategy": strategy,
                "deadline_at": None
                if instance.deadline_at is None
                else instance.deadline_at.isoformat(),
            },
            instance.id,
        )
        if strategy == "mark":
            instance.deadline_marked = True
            return
        states = (TokenState.UNFINISHED.value,)
        for token in kernel.tokens(ctx.session, instance.id, states=states):
            kernel.cancel_token(ctx.session, token)
        end_status = "cancelled" if strategy == "cancel" else "terminated"
        kernel.complete_instance(ctx.session, instance, end_status)
        # 终态事件与 runtime 路径同款（消费端统一感知实例因何终态）
        terminal_event = (
            DomainEvent.PROCESS_CANCELLED
            if strategy == "cancel"
            else DomainEvent.PROCESS_TERMINATED
        )
        kernel.record_event(
            terminal_event,
            {"actor": "system:deadline", "reason": "instance_deadline"},
            instance.id,
        )

    def _execute_handover(
        self, ctx: CommandContext, job: JobRow, task: TaskRow, strategy: str
    ) -> None:
        """reassign（按目标改派）/ escalate（自动转上级，查无上级→故障单）。"""
        kernel = self._engine.kernel_for(ctx)
        if strategy == "reassign":
            target = str(job.payload.get("reassign_to") or "")
            if not target:
                raise EngineExecutionError(f"reassign deadline 缺少改派目标（job={job.id}）")
        else:  # escalate：宿主目录的上级；查无 → 故障单（不静默丢，spec §5.1）
            org = self._engine.config.org
            if org is None:
                raise EngineExecutionError(
                    f"escalate 需要组织目录（EngineConfig.org），job={job.id}"
                )
            target_manager = org.manager_of(task.assignee)
            if not target_manager:
                raise EngineExecutionError(
                    f"escalate 查无上级：{task.assignee}（job={job.id}）→ 故障单"
                )
            target = target_manager
            kernel.record_event(
                DomainEvent.TASK_ESCALATED,
                {"task_id": task.id, "from": task.assignee, "to": target},
                job.instance_id,
            )
        self._engine.task._transfer(ctx, task.id, target, "system:timeout", f"超时{strategy}")

    def _execute_strategy(self, ctx: CommandContext, strategy: str, task_id: str) -> None:
        """对在办任务执行策略（任务不存在由调用方跳过）。"""
        task = self._engine.task
        if strategy == "auto_approve":
            task.complete_in_ctx(
                ctx, task_id, actor="system:timeout", comment="超时自动通过", as_agent=True
            )
        elif strategy == "auto_reject":
            task.reject_in_ctx(
                ctx, task_id, actor="system:timeout", comment="超时自动驳回", as_agent=True
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
