"""单实例强化守护测试：统一 touch 守卫 / Job 原子认领 / UTC 时钟 / 调度接线。

- 写命令全覆盖：任一写操作后实例行 version 必 +1（乐观锁串行化点生效）；
- 双调度器恰一次：并发 tick 同一 Job，CAS 认领保证只执行一次；
- 丢失更新拦截：基于旧快照的第二笔写提交时必触发 StaleDataError。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from flowork.clock import utc_now
from flowork.definitions.builder import FlowBuilder
from flowork.definitions.node import MultiInstance, TimeoutPolicy
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.persistence.orm import ProcessInstanceRow, TokenRow


class FakeClock:
    """可推进的确定性时钟（引擎 config.now 注入）。"""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 9, 0, 0)

    def __call__(self) -> datetime:
        return self._now

    def advance_minutes(self, minutes: int) -> None:
        self._now += timedelta(minutes=minutes)


# —— 夹具流程 ——


def _linear(code: str) -> FlowBuilder:
    return (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )


def _chain() -> FlowBuilder:
    return (
        FlowBuilder("hs_chain")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
    )


def _cand() -> FlowBuilder:
    return (
        FlowBuilder("hs_cand")
        .start()
        .user_task("c", candidate_users=["'u1'", "'u2'"])
        .end()
        .connect("start", "c")
        .connect("c", "end")
    )


def _sign() -> FlowBuilder:
    return (
        FlowBuilder("hs_sign")
        .start()
        .user_task(
            "s",
            assignees=["'u1'", "'u2'"],
            multi_instance=MultiInstance(
                mode="parallel",
                approve_exit="task.approve_count >= task.item_count",
                disapprove_exit="task.disapprove_count >= 1",
            ),
        )
        .end()
        .connect("start", "s")
        .connect("s", "end")
    )


def _cc() -> FlowBuilder:
    return (
        FlowBuilder("hs_cc")
        .start()
        .cc_task("cc", recipients=["'u_cc'"])
        .end()
        .connect("start", "cc")
        .connect("cc", "end")
    )


def _timeout(code: str, strategy: str) -> FlowBuilder:
    return (
        FlowBuilder(code)
        .start()
        .user_task(
            "slow",
            assignee="'u_slow'",
            timeout=TimeoutPolicy(due_minutes=60, strategy=strategy),
        )
        .end()
        .connect("start", "slow")
        .connect("slow", "end")
    )


def _engine(db: Path, *, now: Callable[[], datetime] | None = None) -> ProcessEngine:
    engine = ProcessEngine(EngineConfig(db_file=db, now=now or utc_now))
    for builder in (
        _linear("hs_linear"),
        _chain(),
        _cand(),
        _sign(),
        _cc(),
        _timeout("hs_notify", "notify"),
        _timeout("hs_auto_approve", "auto_approve"),
    ):
        definition = builder.build()
        engine.repository.deploy(definition)
    return engine


def _version(engine: ProcessEngine, instance_id: str) -> int:
    def _command(ctx: Any) -> int:
        row = ctx.session.get(ProcessInstanceRow, instance_id)
        assert row is not None
        return row.version

    return engine.execute(_command)


def _take(engine: ProcessEngine, instance_id: str, assignee: str):
    return engine.task.list_tasks(instance_id=instance_id, assignee=assignee)[0]


# —— 统一 touch 守卫：每个写操作必 bump 实例版本 ——


def _case_complete(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_linear", start_user="u_s")
    task = _take(engine, iid, "u_a")
    return iid, lambda: engine.task.complete(task.id, actor="u_a")


def _case_reject(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_chain", start_user="u_s")
    first = _take(engine, iid, "u_a")
    engine.task.complete(first.id, actor="u_a")
    second = _take(engine, iid, "u_b")
    return iid, lambda: engine.task.reject(second.id, actor="u_b", comment="退回")


def _case_claim(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_cand", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]
    return iid, lambda: engine.task.claim(task.id, "u1")


def _case_unclaim(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_cand", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]
    engine.task.claim(task.id, "u1")
    return iid, lambda: engine.task.unclaim(task.id, "u1")


def _case_transfer(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_linear", start_user="u_s")
    task = _take(engine, iid, "u_a")
    return iid, lambda: engine.task.transfer(task.id, to_user="u_x", actor="u_a")


def _case_assist(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_linear", start_user="u_s")
    task = _take(engine, iid, "u_a")
    return iid, lambda: engine.task.assist(task.id, users=["u_h"], actor="u_a")


def _case_complete_assist(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_linear", start_user="u_s")
    task = _take(engine, iid, "u_a")
    engine.task.assist(task.id, users=["u_h"], actor="u_a")
    helper = _take(engine, iid, "u_h")
    return iid, lambda: engine.task.complete_assist(helper.id, actor="u_h")


def _case_add_countersigner(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_sign", start_user="u_s")
    task = _take(engine, iid, "u1")
    return iid, lambda: engine.task.add_countersigner(task.id, users=["u3"], actor="u_admin")


def _case_withdraw(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_chain", start_user="u_s")
    first = _take(engine, iid, "u_a")
    engine.task.complete(first.id, actor="u_a")
    return iid, lambda: engine.task.withdraw(first.id, actor="u_a")


def _case_activate_node(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_chain", start_user="u_s")
    return iid, lambda: engine.task.activate_node(iid, "b", actor="u_admin")


def _case_cancel_node(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_linear", start_user="u_s")
    return iid, lambda: engine.task.cancel_node(iid, "a", actor="u_admin")


def _case_finish_instance(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_linear", start_user="u_s")
    return iid, lambda: engine.task.finish_instance(iid, actor="u_admin")


def _case_move_token(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_chain", start_user="u_s")

    def _token_id() -> str:
        def _command(ctx: Any) -> str:
            row = ctx.session.scalars(
                select(TokenRow).where(TokenRow.instance_id == iid, TokenRow.state == "unfinished")
            ).first()
            assert row is not None
            return row.id

        return engine.execute(_command)

    token_id = _token_id()
    return iid, lambda: engine.runtime.move_token(token_id, "b", actor="u_admin")


def _case_mark_cc_read(engine: ProcessEngine) -> tuple[str, Callable[[], object]]:
    iid = engine.runtime.start_process("hs_cc", start_user="u_s")
    cc = engine.task.list_cc_tasks(recipient="u_cc")[0]
    return iid, lambda: engine.task.mark_cc_read(cc.id, "u_cc")


_CASES: dict[str, Callable[[ProcessEngine], tuple[str, Callable[[], object]]]] = {
    "complete": _case_complete,
    "reject": _case_reject,
    "claim": _case_claim,
    "unclaim": _case_unclaim,
    "transfer": _case_transfer,
    "assist": _case_assist,
    "complete_assist": _case_complete_assist,
    "add_countersigner": _case_add_countersigner,
    "withdraw": _case_withdraw,
    "activate_node": _case_activate_node,
    "cancel_node": _case_cancel_node,
    "finish_instance": _case_finish_instance,
    "move_token": _case_move_token,
    "mark_cc_read": _case_mark_cc_read,
}


@pytest.mark.parametrize("op_name", sorted(_CASES))
def test_write_op_bumps_instance_version(tmp_path: Path, op_name: str):
    """统一 touch 守卫：任一写命令提交都写实例行 → version 递增（乐观锁生效）。

    递增次数即命令内实例行的 UPDATE 次数（多 flush 点的命令可 >1），
    关键保证是"必有写入"——不触碰实例行的写命令不会产生任何版本变化。
    """
    engine = _engine(tmp_path / f"{op_name}.db")
    try:
        instance_id, op = _CASES[op_name](engine)
        before = _version(engine, instance_id)
        op()
        assert _version(engine, instance_id) > before
    finally:
        engine.close()


def test_claim_lost_update_blocked_by_optimistic_lock(tmp_path: Path):
    """基于旧快照的并发写：第二笔提交触发 StaleDataError（而非静默覆盖）。"""
    from flowork.persistence.orm import TaskRow

    engine = _engine(tmp_path / "lost.db")
    try:
        iid = engine.runtime.start_process("hs_cand", start_user="u_s")
        task = engine.task.list_tasks(instance_id=iid)[0]

        s1 = engine.session_factory()
        s2 = engine.session_factory()
        # 双方各自读到同一快照（任务 ready、实例 version=v）
        with s1.begin():
            t1 = s1.get(TaskRow, task.id)
            i1 = s1.get(ProcessInstanceRow, iid)
        with s2.begin():
            t2 = s2.get(TaskRow, task.id)
            i2 = s2.get(ProcessInstanceRow, iid)

        # S1 认领并提交（写任务行 + 触碰实例行）；属性修改会自动开启新事务
        assert t1 is not None and i1 is not None
        t1.assignee = "u1"
        i1.updated_at = engine.config.now()
        s1.commit()

        # S2 基于旧版本提交 → 实例行 UPDATE 命中 0 行 → StaleDataError
        assert t2 is not None and i2 is not None
        t2.assignee = "u2"
        i2.updated_at = engine.config.now()
        with pytest.raises(StaleDataError):
            s2.commit()

        def _assignee(ctx: Any) -> str:
            row = ctx.session.get(TaskRow, task.id)
            assert row is not None
            return row.assignee

        assert engine.execute(_assignee) == "u1"  # 先提交者胜，后写者被拒
        s1.close()
        s2.close()
    finally:
        engine.close()


# —— Job 原子认领：双调度器恰一次 ——


def test_concurrent_ticks_execute_job_once_notify(tmp_path: Path):
    """两个调度器并发 tick 同一 notify Job：恰一个执行（事件只发一条）。"""
    clock = FakeClock()
    db = tmp_path / "tick_notify.db"
    engine = _engine(db, now=clock)
    iid = engine.runtime.start_process("hs_notify", start_user="u_s")
    engine.close()
    clock.advance_minutes(61)

    engine_a = ProcessEngine(EngineConfig(db_file=db, now=clock))
    engine_b = ProcessEngine(EngineConfig(db_file=db, now=clock))
    results: dict[str, int] = {}

    def _run(name: str, eng: ProcessEngine) -> None:
        results[name] = eng.jobs.tick()

    try:
        threads = [
            threading.Thread(target=_run, args=("a", engine_a)),
            threading.Thread(target=_run, args=("b", engine_b)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(results.values()) == 1  # 恰一个副本执行
        assert engine_a.jobs.tick() + engine_b.jobs.tick() == 0  # 再 tick 无残留

        events = [e.event_name for e in engine_a.history.events(iid)]
        assert events.count("timeout_warned") == 1
    finally:
        engine_a.close()
        engine_b.close()


def test_concurrent_ticks_execute_job_once_auto_approve(tmp_path: Path):
    """两个调度器并发 tick 同一 auto_approve Job：任务只被自动办理一次。"""
    clock = FakeClock()
    db = tmp_path / "tick_auto.db"
    engine = _engine(db, now=clock)
    iid = engine.runtime.start_process("hs_auto_approve", start_user="u_s")
    engine.close()
    clock.advance_minutes(61)

    engine_a = ProcessEngine(EngineConfig(db_file=db, now=clock))
    engine_b = ProcessEngine(EngineConfig(db_file=db, now=clock))
    results: dict[str, int] = {}

    def _run(name: str, eng: ProcessEngine) -> None:
        results[name] = eng.jobs.tick()

    try:
        threads = [
            threading.Thread(target=_run, args=("a", engine_a)),
            threading.Thread(target=_run, args=("b", engine_b)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(results.values()) == 1
        assert engine_a.runtime.get_instance(iid).state == "completed"
        agreed = [
            h
            for h in engine_a.history.task_history(iid)
            if h.acted_by == "system:timeout" and h.outcome == "agree"
        ]
        assert len(agreed) == 1  # 无重复自动通过
    finally:
        engine_a.close()
        engine_b.close()


def test_tick_skips_job_not_in_scheduled_state(tmp_path: Path):
    """非 scheduled 状态（如已被认领的 running）不被重复执行。"""
    from sqlalchemy import update

    from flowork.persistence.orm import JobRow

    clock = FakeClock()
    db = tmp_path / "tick_skip.db"
    engine = _engine(db, now=clock)
    try:
        engine.runtime.start_process("hs_notify", start_user="u_s")
        clock.advance_minutes(61)

        # 直接把到期 Job 置为 running（模拟另一副本已认领、尚未完成）
        def _force_running(ctx: Any) -> None:
            ctx.session.execute(update(JobRow).values(state="running"))

        engine.execute(_force_running)
        assert engine.jobs.tick() == 0
    finally:
        engine.close()


# —— UTC 时钟 ——


def test_default_clock_is_naive_utc():
    """默认时钟统一 naive UTC（多副本时间可比；副本以 NTP 对齐）。"""
    config = EngineConfig()
    now = config.now()
    assert now.tzinfo is None
    expected = datetime.now(UTC).replace(tzinfo=None)
    assert abs((now - expected).total_seconds()) < 5
    assert utc_now().tzinfo is None


# —— 调度接线：web 与 CLI ——


def test_web_app_starts_and_stops_scheduler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """设计器的会话库缺省不跑调度（临时库里没有要推进的 Job），显式 FLOWORK_JOBS=on 才开。"""
    from fastapi.testclient import TestClient

    from flowork.contrib.web.app import create_app

    monkeypatch.setenv("FLOWORK_JOBS", "on")
    app = create_app(db_file=tmp_path / "web.db")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert app.state.engine.jobs._scheduler is not None
    assert app.state.engine.jobs._scheduler is None  # 随应用关闭


def test_web_app_scheduler_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from flowork.contrib.web.app import create_app

    monkeypatch.delenv("FLOWORK_JOBS", raising=False)
    app = create_app(db_file=tmp_path / "web_off.db")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert app.state.engine.jobs._scheduler is None


def test_cli_tick_processes_due_jobs(tmp_path: Path):
    from typer.testing import CliRunner

    from flowork.cli.app import app as cli_app

    clock = FakeClock()
    db = tmp_path / "cli.db"
    engine = _engine(db, now=clock)
    engine.runtime.start_process("hs_notify", start_user="u_s")
    engine.close()

    result = CliRunner().invoke(cli_app, ["tick", "--db", str(db)])
    assert result.exit_code == 0
    assert "1" in result.output

    verify = ProcessEngine(EngineConfig(db_file=db))
    try:

        def _remaining(ctx: Any) -> int:
            from flowork.persistence.orm import JobRow

            return len(list(ctx.session.scalars(select(JobRow).where(JobRow.state == "scheduled"))))

        assert verify.execute(_remaining) == 0
    finally:
        verify.close()
