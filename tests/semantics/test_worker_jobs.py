"""P1-2（kernel-v2 spec §5.2/§16）：Job 领取-汇报+租约测试。

验收：租约过期重领——到期未交货的 Job 可被其他 worker 重新领走；
至少一次交付语义下 fail/complete/dead-letter 路径各就各位。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from flowork.clock import utc_now
from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.engine.job_service import JobStateError
from flowork.persistence.orm import IncidentRow, JobRow


@pytest.fixture
def engine() -> ProcessEngine:
    engine = ProcessEngine()
    definition = (
        FlowBuilder("job_flow")
        .start()
        .user_task("a", assignee="u_a")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(definition)
    return engine


def _job(engine: ProcessEngine, instance_id: str) -> str:
    return engine.worker_jobs.create_worker_job(
        job_type="http_call", instance_id=instance_id, payload={"url": "https://x"}
    )


def test_activate_claims_with_lease(engine: ProcessEngine):
    instance_id = engine.runtime.start_process("job_flow", start_user="u_s")
    job_id = _job(engine, instance_id)

    claims = engine.worker_jobs.activate_jobs(["http_call"], worker_id="w-1")
    assert [c.job_id for c in claims] == [job_id]
    assert claims[0].payload == {"url": "https://x"}
    # 再领为空：已被 w-1 租住
    assert engine.worker_jobs.activate_jobs(["http_call"], worker_id="w-2") == []
    with engine.session_factory() as session:
        row = session.get(JobRow, job_id)
        assert row is not None and row.state == "leased" and row.worker_id == "w-1"


def test_complete_releases_and_finishes(engine: ProcessEngine):
    instance_id = engine.runtime.start_process("job_flow", start_user="u_s")
    job_id = _job(engine, instance_id)
    engine.worker_jobs.activate_jobs(["http_call"], worker_id="w-1")
    engine.worker_jobs.complete_job(job_id, worker_id="w-1", output={"status": 200})

    with engine.session_factory() as session:
        row = session.get(JobRow, job_id)
        assert row is not None
        assert row.state == "done" and row.result_payload == {"status": 200}
    with pytest.raises(JobStateError):
        engine.worker_jobs.complete_job(job_id, worker_id="w-1", output={})  # 已终结


def test_fail_reports_retry_then_dead_letter(engine: ProcessEngine):
    instance_id = engine.runtime.start_process("job_flow", start_user="u_s")
    job_id = _job(engine, instance_id)
    engine.worker_jobs.activate_jobs(["http_call"], worker_id="w-1")

    outcome = engine.worker_jobs.fail_job(job_id, worker_id="w-1", error="连接超时")
    assert outcome == "scheduled"
    with engine.session_factory() as session:
        row = session.get(JobRow, job_id)
        assert row is not None and row.retry_count == 1 and row.worker_id is None

    # 重试再领再败，次数耗尽 → dead-letter + 故障单
    engine.worker_jobs.activate_jobs(["http_call"], worker_id="w-1")
    assert (
        engine.worker_jobs.fail_job(job_id, worker_id="w-1", error="仍超时", retries=0)
        == "dead_letter"
    )
    with engine.session_factory() as session:
        row = session.get(JobRow, job_id)
        assert row is not None and row.state == "dead_letter"
        assert (
            session.scalars(
                select(IncidentRow).where(IncidentRow.instance_id == instance_id)
            ).first()
            is not None
        )


def test_lease_expiry_allows_reclaim_by_other_worker(engine: ProcessEngine):
    """验收（P1-2）：租约到期没交货 → 别人可以重新领。"""
    instance_id = engine.runtime.start_process("job_flow", start_user="u_s")
    job_id = _job(engine, instance_id)
    engine.worker_jobs.activate_jobs(["http_call"], worker_id="w-1", visibility_timeout_seconds=60)

    # 未到期：w-2 领不到
    assert engine.worker_jobs.activate_jobs(["http_call"], worker_id="w-2") == []
    # 到期（时间前拨）：w-2 重领成功
    expired = utc_now() - timedelta(seconds=1)
    with engine.session_factory() as session, session.begin():
        session.get(JobRow, job_id).lease_until = expired
    claims = engine.worker_jobs.activate_jobs(["http_call"], worker_id="w-2")
    assert [c.job_id for c in claims] == [job_id]

    # 原 worker 汇报被拒（易主）
    with pytest.raises(JobStateError):
        engine.worker_jobs.complete_job(job_id, worker_id="w-1", output={})
    engine.worker_jobs.complete_job(job_id, worker_id="w-2", output={"by": "w-2"})


def test_worker_job_invisible_to_internal_tick(engine: ProcessEngine):
    """worker 类 Job 不被内部 timer tick 认领（两套执行面互不干扰）。"""
    instance_id = engine.runtime.start_process("job_flow", start_user="u_s")
    _job(engine, instance_id)
    assert engine.jobs.tick() == 0


def test_resolve_incident_retry_requeues_worker_dead_letter(engine: ProcessEngine):
    """跨执行面对拍（REVIEW P0-①回归）：job_service 写入的死信 Job 能被
    resolve_incident(retry) 查到并重派——状态值拼写必须两处一致。"""
    instance_id = engine.runtime.start_process("job_flow", start_user="u_s")
    job_id = _job(engine, instance_id)
    engine.worker_jobs.activate_jobs(["http_call"], worker_id="w-1")
    engine.worker_jobs.fail_job(job_id, worker_id="w-1", error="第一次失败", retries=1)
    engine.worker_jobs.activate_jobs(["http_call"], worker_id="w-1")
    outcome = engine.worker_jobs.fail_job(  # 重试耗尽 → dead_letter + incident
        job_id, worker_id="w-1", error="彻底失败", retries=0
    )
    assert outcome == "dead_letter"

    incident = engine.runtime.list_incidents(instance_id=instance_id)[0]
    engine.runtime.resolve_incident(incident["id"], action="retry", actor="ops")
    with engine.session_factory() as session:
        row = session.get(JobRow, job_id)
        assert row is not None and row.state == "scheduled"  # 死信被重派
    assert engine.runtime.get_instance(instance_id).state == "running"
