"""等待节点构造走查：等时长（消息/竞速形态见 tests/semantics/test_wait_node.py）。"""

from __future__ import annotations

from datetime import datetime, timedelta

from flowork import contrib
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers, covers_node
from tests.suite.world_catalog import build_definition
from tests.suite.world_org import build_static_org


class _Clock:
    """可拨时钟（闭包陷阱规避：测试里 now += 改不动 lambda 捕获的局部）。"""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: int) -> None:
        self.now += timedelta(minutes=minutes)


def _wait_engine() -> ProcessEngine:
    org = build_static_org()
    clock = _Clock(datetime(2026, 10, 9, 9, 0, 0))
    return ProcessEngine(
        EngineConfig(now=clock, org=org, expression_functions=contrib.cel_vocabulary(org))
    )


def _deploy_duration(engine: ProcessEngine) -> None:
    engine.repository.deploy(build_definition("suite_wait_duration"))


@covers("node.wait")
@covers_node("suite_wait_duration", "start", "hold", "wait_end")
def test_wait_duration_advances_with_clock() -> None:
    """等时长：进入起算 after_minutes，拨钟 + tick 到点放行。"""
    engine = _wait_engine()
    _deploy_duration(engine)
    iid = engine.runtime.start_process("suite_wait_duration", start_user="u_dev_wu")
    assert engine.runtime.get_instance(iid).state == "running"

    engine.config.now.advance(4)  # type: ignore[attr-defined]
    engine.jobs.tick()
    assert engine.runtime.get_instance(iid).state == "running"  # 未到点

    engine.config.now.advance(2)  # type: ignore[attr-defined]
    engine.jobs.tick()
    assert engine.runtime.get_instance(iid).state == "completed"
