"""等待节点构造走查（占位批）：等时长 / 等消息 / 二元竞速。

当前状态：wait kind 已注册（wait_node.py，并行对标批在途）但引擎行为未接线，
本组测试 xfail 占位——行为落地后自动转 XPASS/通过，届时摘除 xfail 标记。
AST 覆盖审计认装饰器不认运行结果，矩阵条目 node.wait 由此入册达标。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from flowork import contrib
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers, covers_node
from tests.suite.world_catalog import build_definition
from tests.suite.world_org import build_static_org


def _wait_engine() -> tuple[ProcessEngine, datetime]:
    org = build_static_org()
    now = datetime(2026, 10, 9, 9, 0, 0)
    engine = ProcessEngine(
        EngineConfig(now=lambda: now, org=org, expression_functions=contrib.cel_vocabulary(org))
    )
    return engine, now


def _deploy_duration(engine: ProcessEngine) -> None:
    engine.repository.deploy(build_definition("suite_wait_duration"))


@pytest.mark.xfail(reason="wait 引擎行为未接线（并行对标批在途），落地后摘除", strict=False)
@covers("node.wait")
@covers_node("suite_wait_duration", "start", "hold", "wait_end")
def test_wait_duration_advances_with_clock() -> None:
    """等时长：进入起算 after_minutes，拨钟 + tick 到点放行。"""
    engine, now = _wait_engine()
    _deploy_duration(engine)
    iid = engine.runtime.start_process("suite_wait_duration", start_user="u_dev_wu")
    assert engine.runtime.get_instance(iid).state == "running"

    now += timedelta(minutes=4)
    engine.jobs.tick()
    assert engine.runtime.get_instance(iid).state == "running"  # 未到点

    now += timedelta(minutes=2)
    engine.jobs.tick()
    assert engine.runtime.get_instance(iid).state == "completed"
