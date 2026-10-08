"""P0-5（kernel-v2 spec §11/§16）：启动幂等三策略测试。

验收：并发同 key 竞态——两线程同时以同 key 发起，唯一索引兜底后按策略收口，
最终恰一个实例落库。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from flowork.definitions.builder import FlowBuilder
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.engine.ops import LockConflictError
from flowork.errors import DuplicateStartError
from flowork.persistence.orm import ProcessInstanceRow


@pytest.fixture
def engine() -> ProcessEngine:
    engine = ProcessEngine()
    definition = (
        FlowBuilder("idem_flow")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(definition)
    return engine


def start(engine: ProcessEngine, key: str, reuse: str = "reject") -> str:
    return engine.runtime.start_process(
        "idem_flow",
        start_user="u_s",
        idempotency_key=key,
        reuse=reuse,  # type: ignore[arg-type]
    )


def instance_count(engine: ProcessEngine) -> int:
    with engine.session_factory() as session:
        return int(session.scalar(select(func.count()).select_from(ProcessInstanceRow)) or 0)


def test_reject_strategy_raises_on_duplicate_key(engine: ProcessEngine):
    first = start(engine, "order-1")
    with pytest.raises(DuplicateStartError):
        start(engine, "order-1")
    assert instance_count(engine) == 1
    assert engine.runtime.get_instance(first).state == "running"


def test_return_existing_strategy_returns_same_instance(engine: ProcessEngine):
    first = start(engine, "order-2")
    second = start(engine, "order-2", reuse="return_existing")
    assert second == first
    assert instance_count(engine) == 1


def test_allow_duplicate_strategy_creates_second_instance(engine: ProcessEngine):
    first = start(engine, "order-3")
    second = start(engine, "order-3", reuse="allow_duplicate")
    assert second != first
    assert instance_count(engine) == 2


def test_same_key_different_definitions_do_not_collide(engine: ProcessEngine):
    """联合唯一＝(definition_code, idempotency_key)：跨定义同 key 合法。"""
    other = (
        FlowBuilder("idem_other")
        .start()
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "b")
        .connect("b", "end")
        .build()
    )
    engine.repository.deploy(other)
    a = start(engine, "order-4")
    b = engine.runtime.start_process(
        "idem_other", start_user="u_s", idempotency_key="order-4", reuse="reject"
    )
    assert a != b


def _file_engine(tmp_path) -> ProcessEngine:
    """并发用文件库（WAL）：各线程独立连接，写锁串行/撞唯一索引走兜底路径。"""
    engine = ProcessEngine(EngineConfig(db_file=tmp_path / "race.db"))
    definition = (
        FlowBuilder("idem_flow")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(definition)
    return engine


def test_concurrent_same_key_race_settles_to_one_instance(tmp_path):
    """验收（P0-5）：并发同 key 竞态——唯一索引兜底，恰好一个实例。"""
    engine = _file_engine(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: engine.runtime.start_process(
                    "idem_flow",
                    start_user="u_s",
                    idempotency_key="race-key",
                    reuse="return_existing",
                ),
                range(4),
            )
        )
    assert len(set(results)) == 1  # 全部收口到同一实例
    assert instance_count(engine) == 1


def test_concurrent_same_key_reject_keeps_one_and_raises_others(tmp_path):
    engine = _file_engine(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(
                engine.runtime.start_process,
                "idem_flow",
                start_user="u_s",
                idempotency_key="race-reject",
                reuse="reject",
            )
            for _ in range(4)
        ]
        outcomes = list(futures)
    errors = [f for f in outcomes if f.exception() is not None]
    succeeded = [f for f in outcomes if f.exception() is None]
    assert len(succeeded) >= 1
    for failed in errors:
        assert isinstance(failed.exception(), (DuplicateStartError, LockConflictError))
    assert instance_count(engine) == 1
