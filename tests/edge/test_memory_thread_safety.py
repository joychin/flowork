"""内存引擎线程安全回归（来源：全仓review-2026-11-23 §2.1）。

StaticPool 单连接上并发命令的事务曾互相交错：只读命令的提交会把另一个
线程写到一半的事务一并持久化——失败的命令被部分提交（已办留痕、任务
消失、流程卡死）。修后＝连接守卫事务级串行化：读阻塞到写命令落定、读到
提交前状态；同线程嵌套会话（故障单记录）可重入。
"""

from __future__ import annotations

import threading

from sqlalchemy import select

from flowork.api import EngineExecutionError, FlowBuilder, FloworkError, ProcessEngine
from flowork.persistence.orm import TaskHistoryRow


def _deploy_pause_flow(engine: ProcessEngine, handler) -> str:
    engine.register_handler(handler.__name__, handler)
    engine.repository.deploy(
        FlowBuilder("mem_race")
        .start()
        .user_task("approve", assignee="'u1'", hooks={"on_exit": {"handler": handler.__name__}})
        .end()
        .connect("start", "approve")
        .connect("approve", "end")
        .build()
    )
    return engine.runtime.start_process("mem_race", start_user="u0")


def test_concurrent_read_cannot_commit_failing_write() -> None:
    """写命令钩子内暂停时，并发只读必须排队——失败回滚后无部分提交痕迹。"""
    engine = ProcessEngine()
    paused = threading.Event()
    release = threading.Event()

    def pause_then_fail(ctx, params) -> None:
        paused.set()
        release.wait(30)
        raise RuntimeError("hook boom after pause")

    iid = _deploy_pause_flow(engine, pause_then_fail)
    task = engine.task.list_tasks(instance_id=iid)[0]

    writer_outcome: list[object] = []

    def writer() -> None:
        try:
            engine.task.complete(task.id, actor="u1")
            writer_outcome.append("ok")
        except Exception as exc:
            writer_outcome.append(exc)

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    assert paused.wait(10), "钩子未到达暂停点"

    seen: list[list] = []

    def reader() -> None:
        seen.append(engine.task.list_tasks(instance_id=iid))

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    reader_thread.join(1.5)
    assert reader_thread.is_alive(), "读命令必须阻塞到写命令落定（修前读到未提交半成品）"

    release.set()
    writer_thread.join(30)
    reader_thread.join(30)

    assert isinstance(writer_outcome[0], EngineExecutionError)  # 钩子失败＝技术故障
    assert len(seen[0]) == 1 and seen[0][0].id == task.id  # 读到提交前状态
    assert [t.id for t in engine.task.list_tasks(instance_id=iid)] == [task.id]

    session = engine.session_factory()
    try:
        assert session.scalars(select(TaskHistoryRow)).first() is None, "回滚后不得有已办留痕"
    finally:
        session.close()


def test_concurrent_completes_exactly_one_wins_on_memory() -> None:
    """多线程抢办同一任务：恰一次成功、其余领域拒绝、无意外异常。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("mem_claim")
        .start()
        .user_task("approve", assignee="'u1'")
        .end()
        .connect("start", "approve")
        .connect("approve", "end")
        .build()
    )
    iid = engine.runtime.start_process("mem_claim", start_user="u0")
    task = engine.task.list_tasks(instance_id=iid)[0]

    outcomes: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def compete(i: int) -> None:
        barrier.wait(10)
        try:
            engine.task.complete(task.id, actor="u1", comment=f"c{i}")
            verdict = "ok"
        except FloworkError:
            verdict = "rejected"
        except Exception as exc:
            verdict = f"unexpected:{type(exc).__name__}"
        with lock:
            outcomes.append(verdict)

    threads = [threading.Thread(target=compete, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert outcomes.count("ok") == 1
    assert all(not o.startswith("unexpected") for o in outcomes), outcomes
    assert engine.runtime.get_instance(iid).state == "completed"
