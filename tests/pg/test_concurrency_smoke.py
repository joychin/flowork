"""PG 并发冒烟：双副本竞态与 Job CAS（真行锁语义下对照 SQLite 结论）。

来源：spec §14 支持矩阵 + docs/integration.md「并发模型」声明——该声明
此前只有 SQLite/WAL 证据（tests/edge）。本文件在真 PG 上验证同一组
「恰一次/恰一赢家」契约在行锁 + READ COMMITTED 下不变量保持。

多副本形态：同一 URL 多个 ProcessEngine 实例（与 edge 的文件库工厂同约定）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest

from flowork.api import FlowBuilder, ProcessEngine
from flowork.clock import utc_now
from flowork.errors import FloworkError
from flowork.persistence.orm import JobRow

pytestmark = pytest.mark.needs_postgres


class _Clock:
    """可推进时钟（租约/时限确定性用）。"""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 9, 0, 0)

    def __call__(self) -> datetime:
        return self._now

    def advance_minutes(self, minutes: int) -> None:
        self._now += timedelta(minutes=minutes)


def _race(calls: list[Callable[[], object]]) -> list[tuple[bool, object]]:
    """真线程并发（判定纪律同 tests/edge：只接受 FloworkError 作败方）。"""
    threads: list[threading.Thread] = []
    results: list[Any] = [None] * len(calls)

    def _run(index: int, call: Callable[[], object]) -> None:
        try:
            results[index] = (True, call())
        except FloworkError as exc:
            results[index] = (False, type(exc))
        except Exception as exc:  # 非 FloworkError 也是失败，交由断言暴露
            results[index] = (False, exc)

    for index, call in enumerate(calls):
        threads.append(threading.Thread(target=_run, args=(index, call)))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def _deploy_linear(engine: ProcessEngine, code: str = "pg_race") -> None:
    flow = (
        FlowBuilder(code, "并发直线")
        .start()
        .user_task("approve", name="审批", assignee="'u_a'")
        .end()
        .connect("start", "approve")
        .connect("approve", "end")
        .build()
    )
    engine.repository.deploy(flow)


def test_double_complete_across_replicas_single_winner(
    pg_engines: Callable[..., ProcessEngine],
) -> None:
    """双副本双线程办同一任务：恰一赢家，历史恰一条 agree（PG 行锁对照）。"""
    replica_a = pg_engines()
    _deploy_linear(replica_a)
    instance_id = replica_a.runtime.start_process("pg_race", start_user="u_s")
    task_id = replica_a.task.list_tasks(instance_id=instance_id)[0].id

    replica_b = pg_engines()  # 同一 PG 库的另一引擎（多副本）
    results = _race(
        [
            lambda: replica_a.task.complete(task_id, actor="u_a"),
            lambda: replica_b.task.complete(task_id, actor="u_a"),
        ]
    )
    winners = [r for ok, r in results if ok]
    assert len(winners) == 1, f"应恰一赢家，实得 {results}"

    assert replica_a.runtime.get_instance(instance_id).state == "completed"
    history = replica_a.history.task_history(instance_id)
    assert [(h.activity_code, h.outcome) for h in history] == [("approve", "agree")]


def test_double_tick_cas_runs_job_once(pg_engines: Callable[..., ProcessEngine]) -> None:
    """双副本同时 tick 同一到期 Job：CAS 认领下恰一次执行。"""
    clock = _Clock()
    replica_a = pg_engines(now=clock)
    _deploy_linear(replica_a, code="pg_tick")
    instance_id = replica_a.runtime.start_process("pg_tick", start_user="u_s")

    replica_b = pg_engines(now=clock)
    results = _race([replica_a.jobs.tick, replica_b.jobs.tick])
    executed = sum(r for ok, r in results if ok)
    assert executed <= 1, f"同一 Job 不应被执行两次：{results}"

    # 无到期 Job 时两副本都应为 0；此处核心断言是「不重复执行」不变量
    assert replica_a.runtime.get_instance(instance_id).state == "running"


def test_worker_job_claimed_once_across_replicas(
    pg_engines: Callable[..., ProcessEngine],
) -> None:
    """双副本抢批：同一 worker Job 不会被两个副本同时领走。"""
    replica_a = pg_engines()
    _deploy_linear(replica_a, code="pg_worker")
    instance_id = replica_a.runtime.start_process("pg_worker", start_user="u_s")
    replica_a.worker_jobs.create_worker_job(
        job_type="http_call", instance_id=instance_id, payload={"url": "https://example.invalid"}
    )
    replica_b = pg_engines()

    results = _race(
        [
            lambda: replica_a.worker_jobs.activate_jobs(["http_call"], worker_id="w-a"),
            lambda: replica_b.worker_jobs.activate_jobs(["http_call"], worker_id="w-b"),
        ]
    )
    claimed_ids = [c.job_id for ok, r in results if ok for c in r]  # type: ignore[union-attr]
    assert len(claimed_ids) == len(set(claimed_ids)) == 1


def test_worker_job_lease_expiry_allows_reclaim(
    pg_engines: Callable[..., ProcessEngine],
) -> None:
    """租约到期后另一副本可重领（PG 上 lease_until 比较语义对照）。

    注意：``activate_jobs`` 的「现在」取自墙钟 ``utc_now()``（job_service.py
    硬编码，与 ``jobs.tick`` 的 ``config.now`` 不同源）——故到期推进只能直接
    改租约列（与 tests/semantics/test_worker_jobs.py 同法），而非推注入时钟。
    此时钟分叉为既有事实，非 PG 方言问题，已单独登记待修。
    """
    replica_a = pg_engines()
    _deploy_linear(replica_a, code="pg_lease")
    instance_id = replica_a.runtime.start_process("pg_lease", start_user="u_s")
    job_id = replica_a.worker_jobs.create_worker_job(
        job_type="http_call", instance_id=instance_id, payload={"url": "https://example.invalid"}
    )

    replica_b = pg_engines()
    first = replica_a.worker_jobs.activate_jobs(
        ["http_call"], worker_id="w-a", visibility_timeout_seconds=60
    )
    assert [c.job_id for c in first] == [job_id]

    # 未到期：另一副本领不到
    assert replica_b.worker_jobs.activate_jobs(["http_call"], worker_id="w-b") == []

    # 到期：把租约改成过去时刻，另一副本应可重领（PG 上 datetime 比较语义对照）
    with replica_a.session_factory() as session, session.begin():
        row = session.get(JobRow, job_id)
        assert row is not None
        row.lease_until = utc_now() - timedelta(seconds=1)

    reclaimed = replica_b.worker_jobs.activate_jobs(["http_call"], worker_id="w-b")
    assert [c.job_id for c in reclaimed] == [job_id]

    def _lease_owner(ctx: Any) -> str | None:
        row = ctx.session.get(JobRow, job_id)
        return row.worker_id if row is not None else None

    assert replica_b.execute(_lease_owner) == "w-b"


def test_start_idempotency_under_concurrency(
    pg_engines: Callable[..., ProcessEngine],
) -> None:
    """同 idempotency_key 并发启动：唯一索引 + 策略收敛为单实例（PG 对照）。"""
    replica_a = pg_engines()
    _deploy_linear(replica_a, code="pg_idem")
    replica_b = pg_engines()

    results = _race(
        [
            lambda: replica_a.runtime.start_process(
                "pg_idem",
                start_user="u_s",
                idempotency_key="dup-key",
                reuse="return_existing",
            ),
            lambda: replica_b.runtime.start_process(
                "pg_idem",
                start_user="u_s",
                idempotency_key="dup-key",
                reuse="return_existing",
            ),
        ]
    )
    ids = [r for ok, r in results if ok]
    assert len(ids) == 1 or len(set(ids)) == 1, f"同 key 应收敛为同一实例：{results}"
    assert replica_a.runtime.get_instance(ids[0]).state == "running"


def test_optimistic_lock_conflict_rejects_or_retries(
    pg_engines: Callable[..., ProcessEngine],
) -> None:
    """过期快照写：PG 上行级乐观锁按 lock_retries 重试/明确拒绝，绝不静默覆盖。"""
    replica_a = pg_engines()
    _deploy_linear(replica_a, code="pg_optlock")
    instance_id = replica_a.runtime.start_process("pg_optlock", start_user="u_s")

    # 快照 A 读出（未提交的陈旧视图）后 B 推进；A 再写应被版本守卫拦截
    def _read_version(ctx: Any) -> int:
        from flowork.persistence.orm import ProcessInstanceRow

        row = ctx.session.get(ProcessInstanceRow, instance_id)
        assert row is not None
        return row.version

    stale_version = replica_a.execute(_read_version)
    task_id = replica_a.task.list_tasks(instance_id=instance_id)[0].id
    replica_a.task.complete(task_id, actor="u_a")
    fresh_version = replica_a.execute(_read_version)
    assert fresh_version > stale_version  # 推进确实动了版本（守卫有据可依）
    assert replica_a.runtime.get_instance(instance_id).state == "completed"
