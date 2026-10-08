"""tests/edge 公共夹具：竞态测试的文件库引擎工厂与可推进时钟。

既有文件（test_hardening / test_edge_stress）保持自含不动；新竞态测试组
统一走这里的工厂——竞态测试必须用文件库（内存库不跨引擎可见，多副本
形态无法成立）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from flowork.clock import utc_now
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine


class FakeClock:
    """可推进的确定性时钟（引擎 config.now 注入）。"""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 9, 0, 0)

    def __call__(self) -> datetime:
        return self._now

    def advance_minutes(self, minutes: int) -> None:
        self._now += timedelta(minutes=minutes)


@pytest.fixture()
def edge_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def edge_engines(tmp_path: Path):
    """文件库引擎工厂：**同名即同库**——多副本形态用同一 db_name 多次创建，
    得到指向同一文件库的独立引擎对象。
    """
    created: list[ProcessEngine] = []

    def _make(db_name: str, *, now: Callable[[], datetime] | None = None) -> ProcessEngine:
        engine = ProcessEngine(EngineConfig(db_file=tmp_path / f"{db_name}.db", now=now or utc_now))
        created.append(engine)
        return engine

    yield _make
    for engine in created:
        engine.close()
