"""套件·等待世界：wait kind 的最小形态（等时长；消息/竞速随行为接线逐步补）。"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition


def wait_duration_definition() -> ProcessDefinition:
    """等时长最小流程：进入起算 5 分钟，拨钟 + tick 到点放行。"""
    return (
        FlowBuilder("suite_wait_duration", "套件·等待时长")
        .start()
        .wait("hold", timeout={"after_minutes": 5})
        .end("wait_end", "等待结束")
        .flow("start", "hold")
        .flow("hold", "wait_end")
        .build()
    )
