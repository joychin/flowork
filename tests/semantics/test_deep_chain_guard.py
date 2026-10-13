"""深直通链递归栈溢出归一（2026-10-13 收尾批 3）。

实证：推进为递归实现（约 7 帧/节点），直通链 200 节点在 start_process 即
RecursionError 裸冒泡——命令边界此前只归一 EngineExecutionError /
ObjectDeletedError，宿主拿到裸 Python 异常、零留痕。本批在 ``_run_once``
归一为技术故障（挂起+故障单；start 阶段整笔回滚时与「首节点失败」
同语义＝孤儿故障单留痕）。迭代化根治挂账内核重排刀（spec §15）。
"""

from __future__ import annotations

import sys

import pytest

from flowork.api import FlowBuilder, ProcessEngine
from flowork.engine.kernel import EngineExecutionError


def _deep_chain(code: str, count: int):
    b = FlowBuilder(code, name="deep").start("s")
    prev = "s"
    for i in range(count):
        node = f"t{i}"
        b.service_task(node)
        b.connect(prev, node)
        prev = node
    b.end("e")
    b.connect(prev, "e")
    return b.build()


def test_deep_chain_raises_engine_error_with_incident():
    """爆栈必被归一为 EngineExecutionError + 故障单留痕（而非裸 RecursionError）。

    环境怪癖（实测）：celpy.CELPy.__init__ 会把进程级递归上限抬到 2500
    （第三方副作用）——全量套件里 celpy 先被加载时 300 节点（约 7 帧/节点
    ≈1950 帧）压不爆 2500。故本用例显式收紧上限让爆点确定：验证的是
    「爆了必被归一」，不依赖特定阈值。
    """
    engine = ProcessEngine()
    engine.repository.deploy(_deep_chain("deepcrash", 300))
    original = sys.getrecursionlimit()
    sys.setrecursionlimit(600)
    try:
        with pytest.raises(EngineExecutionError, match="推进深度超限"):
            engine.runtime.start_process("deepcrash", start_user="u")
    finally:
        sys.setrecursionlimit(original)
    incidents = engine.runtime.list_incidents()
    assert incidents and "推进深度超限" in incidents[0]["message"]


def test_shallow_chain_still_completes():
    """对照组：常规深度（80 节点）不受归一影响。"""
    engine = ProcessEngine()
    engine.repository.deploy(_deep_chain("deepok", 80))
    iid = engine.runtime.start_process("deepok", start_user="u")
    assert engine.runtime.get_instance(iid).state == "completed"
