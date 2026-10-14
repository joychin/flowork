"""随机流程结构测试（randomized，默认排除）：任意合法结构都收敛到干净终态。

与固定世界的逐步断言互补——这里只断言跨结构不变量：
全库无悬空（实例/令牌/待办）、历史无重复任务、路由行必有择道、
子实例存在性与计划一致、输出映射回传到位。
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
from tests.suite.world_org import build_static_org

BASE_SEED = 20261009


def _iter_count() -> int:
    return int(os.environ.get("FLOWORK_RANDOM_ITER", "30"))


def _engine() -> ProcessEngine:
    org = build_static_org()
    engine = ProcessEngine(EngineConfig(org=org, expression_functions=contrib.cel_vocabulary(org)))

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


def _apply(engine: ProcessEngine, iid: str, step: Step) -> None:
    # 子流程步的待办在子实例下；驳回重放可产生多个子实例，取运行中的那个
    target = iid
    if step.node == "pick":
        running = [
            r.id for r in engine.history.instances() if r.parent_id == iid and r.state == "running"
        ]
        assert len(running) == 1, f"子流程步应有唯一运行中子实例：{running}"
        target = running[0]
    task = _take(engine, target, step.actor, step.node)
    if step.action == "complete":
        engine.task.complete(task.id, actor=step.actor)
    else:
        engine.task.reject(task.id, actor=step.actor)


def _assert_invariants(engine: ProcessEngine, iid: str, world: Generated) -> None:
    assert_full_ledger(engine)  # 悬空+树闭合+逐实例票数/对账/终态契约
    instances = engine.history.instances()
    for row in instances:
        task_ids = [h.task_id for h in engine.history.task_history(instance_id=row.id)]
        assert len(task_ids) == len(set(task_ids)), f"历史重复任务：{row.id}"
    from tests.suite.sequence_audit import route_rows

    for row in instances:
        for decision in route_rows(engine, row.id):
            if decision["edges"]:  # 条件行必有择道；无出边行（终局）selected 为空属正常
                assert decision["selected"], f"条件路由行无择道：{decision}"
    children = [r for r in instances if r.parent_id == iid]
    if world.expects_child:
        # 驳回重放可多次经过 call：至少一个子实例（全终态由 no_dangling 保证）
        assert len(children) >= 1
    else:
        assert len(children) == 0
    if world.expects_child:
        variables = engine.runtime.variables_of(iid)
        assert variables["echo_back"] == world.n  # 子→父输出映射回传


def _failure_report(world: Generated, failed_index: int, exc: Exception) -> str:
    lines = [f"随机结构失败 seed={world.seed} n={world.n}（步 {failed_index}）：{exc!r}", ""]
    lines.append("── 定义 JSON（复现：generate(seed) 后按此部署）──")
    for code, definition in world.definitions.items():
        lines.append(f"--- {code} ---")
        lines.append(definition.model_dump_json())
    lines.append("── 执行计划 ──")
    for index, step in enumerate(world.steps):
        marker = "--failed here-->" if index == failed_index else "               "
        lines.append(f"{marker} {index:3d} {step.action:8s} {step.actor} @{step.node}")
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
