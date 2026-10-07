"""Job 领取-汇报协议（kernel-v2 spec §5.2/P1-2）：外部 worker 的租约式执行。

对齐 Zeebe 语义：``activate_jobs`` 批量领活（领到即「租」住，写 worker_id +
lease_until）；干完 ``complete_job``；失败 ``fail_job``（剩余重试次数由 worker
自报，指数退避）；**租约到期没交货 → 别人可以重新领**（至少一次交付，worker
必须自己幂等——文档级承诺）。次数耗尽 → dead-letter + 故障单。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from flowork.clock import utc_now
from flowork.engine.ops import CommandContext
from flowork.errors import FloworkError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import JobRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.engine.engine import ProcessEngine

__all__ = ["JobClaim", "JobService", "JobStateError"]

_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_MULTIPLIER = 100.0


class JobStateError(FloworkError):
    """Job 状态与操作不符（已完/已被他人持有/不存在）。"""


class JobClaim:
    """一次领取的结果（脱壳的 Job 视图，离会话可用）。

    max_retries＝本 Job 允许的失败总次数上限（fail_job 的 retries 参数才是
    worker 自报的剩余次数）。
    """

    def __init__(
        self, job_id: str, job_type: str, payload: dict[str, Any], max_retries: int
    ) -> None:
        self.job_id = job_id
        self.job_type = job_type
        self.payload = payload
        self.max_retries = max_retries

    def __repr__(self) -> str:  # pragma: no cover - 调试便利
        return f"JobClaim({self.job_id}, {self.job_type}, max_retries={self.max_retries})"


class JobService:
    """外部 worker 的领取-汇报面（spec §11 系统组）。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine

    def create_worker_job(
        self,
        *,
        job_type: str,
        instance_id: str,
        payload: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> str:
        """创建外部执行 Job（service_task mode=worker 的引擎侧落点）。"""

        def _command(ctx: CommandContext) -> str:
            job_id = uuid.uuid4().hex
            ctx.session.add(
                JobRow(
                    id=job_id,
                    job_type=job_type,
                    job_kind="worker",
                    state="scheduled",
                    run_at=utc_now(),
                    max_retries=max_retries,
                    instance_id=instance_id,
                    payload=dict(payload or {}),
                )
            )
            ctx.session.flush()
            return job_id

        return self._engine.execute(_command)

    def activate_jobs(
        self,
        job_types: list[str],
        *,
        worker_id: str,
        max: int = 10,
        visibility_timeout_seconds: int = 300,
    ) -> list[JobClaim]:
        """批量领活：领到即租住（worker_id + lease_until）；到期未交货可被重领。"""

        def _command(ctx: CommandContext) -> list[JobClaim]:
            moment = utc_now()
            # 可领＝未持有（scheduled 且无租约），或租约到期（leased 且 lease_until<now）
            claimable = (JobRow.lease_until.is_(None)) | (JobRow.lease_until < moment)
            candidates = ctx.session.scalars(
                select(JobRow)
                .where(
                    JobRow.job_kind == "worker",
                    JobRow.job_type.in_(job_types),
                    JobRow.state.in_(("scheduled", "leased")),
                    claimable,
                )
                .order_by(JobRow.run_at)
                .limit(max)
            ).all()
            claims: list[JobClaim] = []
            lease_until = moment + timedelta(seconds=visibility_timeout_seconds)
            for job in candidates:
                claimed = ctx.session.execute(
                    update(JobRow)
                    .where(
                        JobRow.id == job.id,
                        JobRow.state.in_(("scheduled", "leased")),
                        claimable,
                    )
                    .values(state="leased", worker_id=worker_id, lease_until=lease_until)
                    .returning(JobRow.id)
                ).scalar_one_or_none()
                if claimed is not None:
                    claims.append(
                        JobClaim(job.id, job.job_type, dict(job.payload or {}), job.max_retries)
                    )
            return claims

        return self._engine.execute(_command)

    def complete_job(self, job_id: str, *, worker_id: str, output: dict[str, Any] | None) -> None:
        """汇报完成：写结果、释放租约、Job 终结。"""

        def _command(ctx: CommandContext) -> None:
            job = self._leased_by(ctx.session, job_id, worker_id)
            job.state = "done"
            job.result_payload = dict(output or {})
            job.worker_id = None
            job.lease_until = None
            ctx.session.flush()

        self._engine.execute(_command)

    def fail_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
        retries: int | None = None,
        backoff_seconds: float | None = None,
    ) -> str:
        """汇报失败：剩余重试次数由 worker 自报（None＝按 JobRow.max_retries 推算）。

        退避：默认初次 1s、每次翻倍、单次上限 100×初次；次数耗尽 →
        dead-letter + incident + 事件（对齐内部 timer 的失败路径）。
        """

        def _command(ctx: CommandContext) -> str:
            moment = utc_now()
            job = self._leased_by(ctx.session, job_id, worker_id)
            job.last_error = error
            job.retry_count += 1
            remaining = retries if retries is not None else (job.max_retries - job.retry_count)
            if remaining <= 0:
                job.state = "dead_letter"
                job.worker_id = None
                job.lease_until = None
                self._engine.runtime._record_incident_in_ctx(
                    ctx,
                    [job.instance_id],
                    FloworkError(f"worker Job {job_id} 重试耗尽：{error}"),
                )
                kernel = self._engine.kernel_for(ctx)
                kernel.record_event(
                    DomainEvent.JOB_DEAD_LETTERED,
                    {"job_id": job_id, "error": error, "worker": True},
                    job.instance_id,
                )
                ctx.session.flush()
                return "dead_letter"
            delay = (
                backoff_seconds
                if backoff_seconds is not None
                else min(
                    _INITIAL_BACKOFF_SECONDS * (2 ** (job.retry_count - 1)),
                    _INITIAL_BACKOFF_SECONDS * _MAX_BACKOFF_MULTIPLIER,
                )
            )
            job.state = "scheduled"
            job.run_at = moment + timedelta(seconds=delay)
            job.next_retry_at = job.run_at
            job.worker_id = None
            job.lease_until = None
            ctx.session.flush()
            return "scheduled"

        return self._engine.execute(_command)

    # —— 内部 ——

    def _leased_by(self, session: Session, job_id: str, worker_id: str) -> JobRow:
        job = session.get(JobRow, job_id)
        if job is None:
            raise JobStateError(f"Job 不存在：{job_id}")
        if job.state == "leased" and job.worker_id not in (None, worker_id):
            raise JobStateError(f"Job {job_id} 由其他 worker 持有（{job.worker_id}）")
        if job.state != "leased":
            raise JobStateError(f"Job {job_id} 状态 {job.state!r} 不可汇报（须先 activate）")
        return job
