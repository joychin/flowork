"""用户动作间并发竞态套件（能力补足 P0-1：G 维「不乱走」的证据广度）。

既有并发证据集中在幂等启动 / CAS 认领 / 乐观锁面（test_hardening、
test_start_idempotency），本组补用户动作间竞态：同实例并发 complete×2 /
complete vs reject / submit vs timer fire / pause vs 推进 / 多 worker 抢批。
每组断言三件事：恰一次推进 + 失败方明确报错 + 历史可解释。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.node import TimeoutPolicy
from flowork.engine.engine import ProcessEngine
from flowork.engine.job_service import JobStateError
from flowork.errors import FloworkError, TaskNotAvailableError
from flowork.persistence.orm import JobRow


def _linear(code: str) -> FlowBuilder:
    """单任务链：complete 即实例终态（pause vs 推进的互斥可判定）。"""
    return (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )


def _chain(code: str) -> FlowBuilder:
    """双任务链：b 可驳回回 a（complete vs reject 的战场）。"""
    return (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
    )


def _timeout_auto(code: str) -> FlowBuilder:
    return (
        FlowBuilder(code)
        .start()
        .user_task(
            "slow",
            assignee="'u_slow'",
            timeout=TimeoutPolicy(due_minutes=60, strategy="auto_approve"),
        )
        .end()
        .connect("start", "slow")
        .connect("slow", "end")
    )


def _deploy_race_flows(engine: ProcessEngine) -> None:
    for builder in (_linear("race_linear"), _chain("race_chain"), _timeout_auto("race_timeout")):
        engine.repository.deploy(builder.build())


def _outcomes(engine: ProcessEngine, instance_id: str, activity: str) -> list[str]:
    return [
        h.outcome for h in engine.history.task_history(instance_id) if h.activity_code == activity
    ]


def _take(engine: ProcessEngine, instance_id: str, assignee: str):
    return engine.task.list_tasks(instance_id=instance_id, assignee=assignee)[0]


def _race(calls: list[Callable[[], object]]) -> list[tuple[bool, object]]:
    """真线程并发执行，收集 (成功?, 返回值或异常)。

    领域异常（FloworkError 族）以类型对象入列；其余异常以实例入列——
    竞态里合法的败方必须是明确的领域异常，随机系统错误直接判测试失败。
    """
    threads: list[threading.Thread] = []
    results: list[Any] = [None] * len(calls)

    def _run(index: int, call: Callable[[], object]) -> None:
        try:
            results[index] = (True, call())
        except FloworkError as exc:
            results[index] = (False, type(exc))
        except Exception as exc:  # 带回主线程判失败（非领域异常即测试失败）
            results[index] = (False, exc)

    for index, call in enumerate(calls):
        threads.append(threading.Thread(target=_run, args=(index, call)))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


_LOSER_TYPES = (TaskNotAvailableError,)


def _assert_exactly_one_winner(results: list[tuple[bool, object]]) -> None:
    winners = [entry for entry in results if entry[0]]
    losers = [entry for entry in results if not entry[0]]
    assert len(winners) == 1, f"恰一个动作生效，实际结果：{results!r}"
    for _, loser in losers:
        if not isinstance(loser, type):
            raise AssertionError(f"竞态中出现非领域异常：{loser!r}")
        assert issubclass(loser, _LOSER_TYPES), f"败方必须是明确报错，实际 {loser!r}"


# —— 组 1：complete×2 ——


def test_concurrent_double_complete_single_engine(edge_engines) -> None:
    """单引擎双线程办同一任务：恰一次推进，败方 TaskNotAvailableError。"""
    engine = edge_engines("dbl")
    _deploy_race_flows(engine)
    iid = engine.runtime.start_process("race_linear", start_user="u_s")
    task = _take(engine, iid, "u_a")

    results = _race(
        [
            lambda: engine.task.complete(task.id, actor="u_a"),
            lambda: engine.task.complete(task.id, actor="u_a"),
        ]
    )
    _assert_exactly_one_winner(results)

    assert engine.runtime.get_instance(iid).state == "completed"
    assert _outcomes(engine, iid, "a").count("agree") == 1  # 无双推进


def test_concurrent_double_complete_two_replicas(edge_engines) -> None:
    """双引擎（多副本形态）同文件双线程办同一任务：先提交者胜。"""
    setup = edge_engines("dbl")
    _deploy_race_flows(setup)
    iid = setup.runtime.start_process("race_linear", start_user="u_s")
    task = _take(setup, iid, "u_a")

    replica_x = edge_engines("dbl")
    replica_y = edge_engines("dbl")
    results = _race(
        [
            lambda: replica_x.task.complete(task.id, actor="u_a"),
            lambda: replica_y.task.complete(task.id, actor="u_a"),
        ]
    )
    _assert_exactly_one_winner(results)

    assert replica_x.runtime.get_instance(iid).state == "completed"
    assert _outcomes(replica_y, iid, "a").count("agree") == 1


# —— 组 2：complete vs reject ——


def test_concurrent_complete_vs_reject(edge_engines) -> None:
    """同一任务并发一同意一驳回：恰一种结局入历史，实例不被双改。"""
    engine = edge_engines("cr")
    _deploy_race_flows(engine)
    iid = engine.runtime.start_process("race_chain", start_user="u_s")
    first = _take(engine, iid, "u_a")
    engine.task.complete(first.id, actor="u_a")
    second = _take(engine, iid, "u_b")

    results = _race(
        [
            lambda: engine.task.complete(second.id, actor="u_b"),
            lambda: engine.task.reject(second.id, actor="u_b", comment="退回"),
        ]
    )
    _assert_exactly_one_winner(results)

    outcomes = _outcomes(engine, iid, "b")
    assert len(outcomes) == 1, "b 站只允许一种结局"
    if outcomes == ["agree"]:
        assert engine.runtime.get_instance(iid).state == "completed"
    else:
        # 驳回生效：实例回 a 站，新任务已复活（cause_token_id 关联）
        assert engine.runtime.get_instance(iid).state == "running"
        revived = [_t.activity_code for _t in engine.task.list_tasks(instance_id=iid)]
        assert revived == ["a"]


def test_reject_after_complete_rejected_deterministic(edge_engines) -> None:
    """确定性交错（双副本顺序）：先 complete 后 reject → 后者明确报错。"""
    setup = edge_engines("cr")
    _deploy_race_flows(setup)
    iid = setup.runtime.start_process("race_chain", start_user="u_s")
    first = _take(setup, iid, "u_a")
    setup.task.complete(first.id, actor="u_a")
    second = _take(setup, iid, "u_b")

    replica = edge_engines("cr")
    setup.task.complete(second.id, actor="u_b")
    with pytest.raises(TaskNotAvailableError):
        replica.task.reject(second.id, actor="u_b", comment="晚到的驳回")

    assert _outcomes(replica, iid, "b") == ["agree"]
    assert replica.runtime.get_instance(iid).state == "completed"


def test_complete_after_reject_rejected_deterministic(edge_engines) -> None:
    """确定性交错（双副本顺序）：先 reject 后 complete → 后者明确报错。"""
    setup = edge_engines("rc")
    _deploy_race_flows(setup)
    iid = setup.runtime.start_process("race_chain", start_user="u_s")
    first = _take(setup, iid, "u_a")
    setup.task.complete(first.id, actor="u_a")
    second = _take(setup, iid, "u_b")

    replica = edge_engines("rc")
    setup.task.reject(second.id, actor="u_b", comment="先到的驳回")
    with pytest.raises(TaskNotAvailableError):
        replica.task.complete(second.id, actor="u_b")

    assert _outcomes(replica, iid, "b") == ["reject"]
    revived = [_t.activity_code for _t in replica.task.list_tasks(instance_id=iid)]
    assert revived == ["a"]


# —— 组 3：submit vs timer fire ——


def test_submit_vs_timer_fire_exactly_once(edge_engines, edge_clock) -> None:
    """超时自动通过与人工办理并发：恰一个结局，不叠加、不重试。"""
    engine = edge_engines("tmr", now=edge_clock)
    _deploy_race_flows(engine)
    iid = engine.runtime.start_process("race_timeout", start_user="u_s")
    task = _take(engine, iid, "u_slow")
    edge_clock.advance_minutes(61)  # 超时点已过

    results = _race(
        [
            lambda: engine.jobs.tick(),
            lambda: engine.task.complete(task.id, actor="u_slow"),
        ]
    )
    for ok, value in results:
        if not ok and isinstance(value, Exception):
            raise AssertionError(f"竞态中出现非领域异常：{value!r}")

    # tick 与人工办理都可能在各自时序里"成功返回"，判据在状态面：
    agrees = [
        h
        for h in engine.history.task_history(iid)
        if h.activity_code == "slow" and h.outcome == "agree"
    ]
    assert len(agrees) == 1, "超时自动通过与人工办理只允许一个生效"
    assert {h.acted_by for h in agrees} <= {"u_slow", "system:timeout"}

    assert engine.runtime.get_instance(iid).state == "completed"
    engine.jobs.tick()  # 排空残留（若竞态中 tick 一方冲突失败，此处补跑跳过逻辑）

    def _job(ctx: Any) -> JobRow:
        return ctx.session.scalars(select(JobRow).where(JobRow.instance_id == iid)).one()

    job = engine.execute(_job)
    assert job.state == "done" and job.retry_count == 0  # 不重试、不死信


# —— 组 4：pause vs 推进 ——


def test_pause_vs_complete_exactly_one_takes_effect(edge_engines) -> None:
    """并发暂停与办理：恰一个生效；暂停胜则恢复后仍可办，历史仍恰一次。"""
    engine = edge_engines("pvc")
    _deploy_race_flows(engine)
    iid = engine.runtime.start_process("race_linear", start_user="u_s")
    task = _take(engine, iid, "u_a")

    results = _race(
        [
            lambda: engine.runtime.pause_process(iid, actor="admin"),
            lambda: engine.task.complete(task.id, actor="u_a"),
        ]
    )
    successes = [entry for entry in results if entry[0]]
    assert len(successes) == 1, "暂停与办理不可同时生效（不允许静默并存）"
    for ok, loser in results:
        if not ok:
            if not isinstance(loser, type):
                raise AssertionError(f"竞态中出现非领域异常：{loser!r}")
            assert issubclass(loser, FloworkError), f"败方必须是领域异常，实际 {loser!r}"

    if engine.runtime.get_instance(iid).state == "paused":
        # 暂停胜出：办理被拦（FloworkError），恢复后放行
        engine.runtime.resume_process(iid, actor="admin")
        engine.task.complete(task.id, actor="u_a")
    assert engine.runtime.get_instance(iid).state == "completed"
    assert _outcomes(engine, iid, "a").count("agree") == 1


# —— 组 5：多 worker 抢同一批 ——


def test_two_workers_activate_same_batch_exclusive(edge_engines) -> None:
    """双 worker 并发领同一批 Job：租约 CAS 互斥——每 Job 恰归一人，易主汇报被拒。"""
    setup = edge_engines("wkr")
    _deploy_race_flows(setup)
    iid = setup.runtime.start_process("race_linear", start_user="u_s")
    job_ids = [
        setup.worker_jobs.create_worker_job(
            job_type="http_call", instance_id=iid, payload={"seq": seq}
        )
        for seq in (1, 2)
    ]

    worker_x = edge_engines("wkr")
    worker_y = edge_engines("wkr")
    claims: dict[str, list[str]] = {"x": [], "y": []}

    def _activate(tag: str, engine: ProcessEngine, worker_id: str) -> None:
        claimed = engine.worker_jobs.activate_jobs(["http_call"], worker_id=worker_id)
        claims[tag] = [c.job_id for c in claimed]

    threads = [
        threading.Thread(target=_activate, args=("x", worker_x, "w-1")),
        threading.Thread(target=_activate, args=("y", worker_y, "w-2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claimed = claims["x"] + claims["y"]
    assert len(claimed) == len(set(claimed)) == 2, "每 Job 恰归一个 worker（无重复领取）"
    assert set(claimed) == set(job_ids)

    # 易主汇报被拒：x 无权交货 y 领走的 Job（y 颗粒无收时互斥已由上面断言覆盖）
    if claims["y"]:
        with pytest.raises(JobStateError):
            worker_x.worker_jobs.complete_job(claims["y"][0], worker_id="w-1", output={})

    # 各自交货，全部终结
    for jid in claims["x"]:
        worker_x.worker_jobs.complete_job(jid, worker_id="w-1", output={"by": "w-1"})
    for jid in claims["y"]:
        worker_y.worker_jobs.complete_job(jid, worker_id="w-2", output={"by": "w-2"})

    def _states(ctx: Any) -> list[str]:
        return list(ctx.session.scalars(select(JobRow.state).where(JobRow.id.in_(job_ids))))

    assert set(worker_x.execute(_states)) == {"done"}
