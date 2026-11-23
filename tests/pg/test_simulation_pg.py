"""PG 上的模拟虚拟时钟回归（全仓review-2026-11-23 P1）。

策略失败的结案留痕曾在命令事务**内**另开会话更新同一 Job 行：PostgreSQL
下同线程自锁永久挂死（外层事务持有行锁且不再前进，死锁检测器看不见）、
文件库忙等耗尽重试且丢失失败证据。本文件钉住「限时返回 + 留痕落库」。
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import select

from flowork.api import FlowBuilder, ProcessEngine, ProcessVariable
from flowork.definitions.node import TaskDeadline
from flowork.persistence.orm import JobRow

pytestmark = pytest.mark.needs_postgres


def test_virtual_strategy_failure_returns_and_traces_on_pg(pg_engine: ProcessEngine) -> None:
    """推钟撞上失败的改派公式：simulate 限时返回，Job 落 done+last_error。"""
    pg_engine.repository.deploy(
        FlowBuilder("pg_vfail")
        .start()
        .user_task(
            "a",
            assignee="'u_a'",
            deadlines=[TaskDeadline(after_minutes=15, action="reassign", reassign_to="nosuchvar")],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(variables=[ProcessVariable(name="nosuchvar", type="number")])
    )

    outcome: dict = {}

    def _run() -> None:
        try:
            outcome["report"] = pg_engine.runtime.simulate(
                "pg_vfail", controls={"decisions": [], "clock": [{"advance_minutes": 30}]}
            )
        except Exception as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=_run)
    worker.start()
    worker.join(60)
    assert not worker.is_alive(), "simulate 在 PG 上挂死——同线程自锁回归"

    report = outcome.get("report")
    assert report is not None, f"simulate 不应抛错：{outcome.get('error')!r}"
    assert any("虚拟时钟兑现失败" in n for n in report.notes)

    session = pg_engine.session_factory()
    try:
        job = session.scalars(select(JobRow)).first()
        assert job is not None and job.state == "done"
        assert "虚拟时钟策略执行失败" in (job.last_error or "")
    finally:
        session.close()
