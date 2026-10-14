"""随机流程结构测试（randomized，默认排除）：任意合法结构都收敛到干净终态。

与固定世界的逐步断言互补——这里只断言跨结构不变量：
全库账本（批次一全家）、路由行必有择道、子实例存在性与计划一致、
call 输出映射回传、mi_call collect 名单对齐。
失败时三段日志（种子+定义 JSON+执行计划并标卡点），可直接复现：
generate(seed) 重放即得同一结构。迭代数 FLOWORK_RANDOM_ITER（默认 30）。
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from flowork import contrib
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from tests.suite.invariants import assert_full_ledger
from tests.suite.random_gen import STARTER, Generated, Step, generate
from tests.suite.sequence_audit import route_rows
from tests.suite.world_flows_time import FakeClock
from tests.suite.world_org import build_static_org

BASE_SEED = 20261009


def _iter_count() -> int:
    return int(os.environ.get("FLOWORK_RANDOM_ITER", "30"))


def _engine() -> ProcessEngine:
    org = build_static_org()
    engine = ProcessEngine(
        EngineConfig(now=FakeClock(), org=org, expression_functions=contrib.cel_vocabulary(org))
    )

    def echo_paid(ctx: Any, params: dict[str, Any]) -> None:
        ctx.variables["erped"] = True

    engine.register_handler("echo_paid", echo_paid)
    return engine


def _take(engine: ProcessEngine, iid: str, actor: str, node: str):
    tasks = [
        t
        for t in engine.task.list_tasks(instance_id=iid, assignee=actor)
        if t.activity_code == node
    ]
    assert tasks, f"无待办：{actor}@{node}"
    return tasks[0]


def _child_task(engine: ProcessEngine, iid: str, actor: str, node: str):
    """子域步：在全部运行中子实例里按节点码找该人的待办（MI 多子顺序无关）。"""
    children = [
        row.id
        for row in engine.history.instances(limit=100_000)
        if row.parent_id == iid and row.state == "running"
    ]
    for child in children:
        tasks = [
            t
            for t in engine.task.list_tasks(instance_id=child, assignee=actor)
            if t.activity_code == node
        ]
        if tasks:
            return tasks[0]
    raise AssertionError(f"子实例无待办：{actor}@{node}（运行中子实例 {len(children)} 个）")


def _apply(engine: ProcessEngine, iid: str, step: Step) -> None:
    if step.action == "advance":
        engine.config.now.advance(step.minutes)  # type: ignore[attr-defined]
        engine.jobs.tick()
        return
    if step.action == "publish":
        assert engine.messages.publish_message(step.message), f"消息未命中订阅：{step.message}"
        return
    target = (
        _child_task(engine, iid, step.actor, step.node)
        if step.scope == "child"
        else _take(engine, iid, step.actor, step.node)
    )
    if step.action == "complete":
        engine.task.complete(target.id, actor=step.actor)
    else:
        engine.task.reject(target.id, actor=step.actor)


def _assert_invariants(engine: ProcessEngine, iid: str, world: Generated) -> None:
    assert_full_ledger(engine)  # 悬空+树闭合+逐实例票数/对账/终态契约
    for row in engine.history.instances(limit=100_000):
        for decision in route_rows(engine, row.id):
            if decision["edges"]:  # 条件行必有择道；无出边行（终局）selected 为空属正常
                assert decision["selected"], f"条件路由行无择道：{decision}"
    children = [r for r in engine.history.instances(limit=100_000) if r.parent_id == iid]
    # 驳回重放可多次经过 call：子实例只会多于下限
    assert len(children) >= world.min_children, (
        f"子实例不足：{len(children)} < {world.min_children}"
    )
    variables = engine.runtime.variables_of(iid)
    if world.has_plain_call:
        assert variables["echo_back"] == world.n  # 子→父输出映射回传
    if world.collected_subjects:
        assert variables["workers"] == world.collected_subjects  # collect 按迭代序对齐


def _failure_report(world: Generated, failed_index: int, exc: Exception) -> str:
    lines = [f"随机结构失败 seed={world.seed} n={world.n}（步 {failed_index}）：{exc!r}", ""]
    lines.append("── 定义 JSON（复现：generate(seed) 后按此部署）──")
    for code, definition in world.definitions.items():
        lines.append(f"--- {code} ---")
        lines.append(definition.model_dump_json())
    lines.append("── 执行计划 ──")
    for index, step in enumerate(world.steps):
        marker = "--failed here-->" if index == failed_index else "               "
        scope = f"[{step.scope}]" if step.scope != "main" else ""
        detail = (
            f"{step.minutes}min"
            if step.action == "advance"
            else step.message
            if step.action == "publish"
            else step.node
        )
        lines.append(f"{marker} {index:3d} {step.action:8s} {step.actor} {scope}{detail}")
    return "\n".join(lines)


@pytest.mark.randomized
def test_random_structures_converge() -> None:
    """任意合法随机结构：执行计划跑完后必收敛到干净终态。"""
    for offset in range(_iter_count()):
        seed = BASE_SEED + offset
        world = generate(seed)
        engine = _engine()
        for definition in world.definitions.values():
            engine.repository.deploy(definition)
        iid = engine.runtime.start_process(
            "rand_main", start_user=STARTER, variables={"n": world.n}
        )
        failed_index = -1
        try:
            for failed_index, step in enumerate(world.steps):  # noqa: B007 —— 记录卡点序号
                _apply(engine, iid, step)
            failed_index = len(world.steps)
            _assert_invariants(engine, iid, world)
        except Exception as exc:  # 随机失败转三段日志再抛
            raise AssertionError(_failure_report(world, failed_index, exc)) from exc
