"""运行形态参数化：同一「超时自动通过」场景在四种形态下行为一致。

形态矩阵：单副本内存库 / 单副本文件库 / 双副本同库（跨副本触发 Job）/
双副本双 tick（CAS 恰一次）。复用 edge_engines 文件库工厂，断言收口在
状态面（终态 completed + 历史恰一条 system:timeout 的 agree）。
目录读缓存无运行开关（写后全清），该维度由 test_org_structure 的
manager_reroute 接缝覆盖，此处不重复。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition
from flowork.definitions.node import TaskDeadline
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine

SHAPES = ["memory", "file", "twin-shared", "twin-double-tick"]


def _timeout_flow() -> ProcessDefinition:
    return (
        FlowBuilder("shape_timeout", "形态·超时自动通过")
        .start()
        .user_task(
            "gate",
            assignee="'u1'",
            deadlines=[TaskDeadline(after_minutes=60, action="auto_approve")],
        )
        .end("shape_end", "形态结束")
        .connect("start", "gate")
        .connect("gate", "shape_end")
        .build()
    )


def _agrees(engine: ProcessEngine, iid: str) -> list:
    return [h for h in engine.history.task_history(iid) if h.outcome == "agree"]


@pytest.mark.parametrize("shape", SHAPES)
def test_timeout_auto_approve_across_shapes(
    shape: str,
    edge_engines: Callable[..., ProcessEngine],
    edge_clock: Callable[[], datetime],
    tmp_path: Path,
) -> None:
    engine_b: ProcessEngine | None = None
    if shape == "memory":
        engine_a = ProcessEngine(EngineConfig(now=edge_clock))
    else:
        db_name = "shape_twin" if shape.startswith("twin") else "shape_file"
        engine_a = edge_engines(db_name, now=edge_clock)
        if shape.startswith("twin"):
            engine_b = edge_engines(db_name, now=edge_clock)  # 同名即同库

    engine_a.repository.deploy(_timeout_flow())
    iid = engine_a.runtime.start_process("shape_timeout", start_user="u0")
    edge_clock.advance_minutes(61)

    if shape == "twin-shared":
        assert engine_b is not None
        assert engine_b.jobs.tick() == 1  # 跨副本：B 触发 A 起的 Job
    elif shape == "twin-double-tick":
        assert engine_b is not None
        assert engine_a.jobs.tick() == 1
        assert engine_b.jobs.tick() == 0  # CAS：第二个 tick 无事可做
    else:
        assert engine_a.jobs.tick() == 1

    assert engine_a.runtime.get_instance(iid).state == "completed", shape
    agrees = _agrees(engine_a, iid)
    assert len(agrees) == 1, f"{shape}：超时自动通过必须恰一次"
    assert agrees[0].acted_by == "system:timeout"
    assert engine_a.jobs.tick() == 0  # 排空后无残留
