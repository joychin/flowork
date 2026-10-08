"""走查演示公共工具：引擎装配、位置快照打印、步进硬校验、预期异常捕获。"""

from __future__ import annotations

from pathlib import Path

from flowork import contrib
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from tests.suite.conftest import deploy_world, register_world_actions
from tests.suite.walkthrough import active_nodes, expect_position, ready_pairs
from tests.suite.world_catalog import STATIC_WORLD_CODES, STORE_WORLD_CODES
from tests.suite.world_flows_time import FakeClock
from tests.suite.world_org import USERS, build_directory_store, build_static_org

NAME_OF = {user_id: name for user_id, name, _dept in USERS}


def who(user: str) -> str:
    return f"{NAME_OF.get(user, user)}({user})"


def make_store_engine(tmp: Path, clock: FakeClock | None = None) -> ProcessEngine:
    """持久化目录世界引擎（可注入可控时钟）。"""
    engine = ProcessEngine(
        EngineConfig(
            now=clock or FakeClock(),
            org=build_directory_store(tmp / "demo_org.db"),
            expression_functions=contrib.cel_vocabulary(build_directory_store(tmp / "demo_org.db")),
        )
    )
    deploy_world(engine, STORE_WORLD_CODES)
    register_world_actions(engine)
    return engine


def make_static_engine() -> ProcessEngine:
    engine = ProcessEngine(
        EngineConfig(
            now=FakeClock(),
            org=build_static_org(),
            expression_functions=contrib.cel_vocabulary(build_static_org()),
        )
    )
    deploy_world(engine, STATIC_WORLD_CODES)
    register_world_actions(engine)
    return engine


def make_bare_engine(*codes: str) -> ProcessEngine:
    """无目录引擎（ad-hoc 图与故障场景用）。"""
    engine = ProcessEngine(EngineConfig(now=FakeClock()))
    deploy_world(engine, list(codes))
    register_world_actions(engine)
    return engine


def snapshot(engine: ProcessEngine, iid: str) -> None:
    info = engine.runtime.get_instance(iid)
    nodes = sorted(active_nodes(engine, iid))
    print(f"    ▶ 实例状态={info.state:<9} 活跃节点={nodes if nodes else '∅（已到终点）'}")
    for activity, assignee in sorted(ready_pairs(engine, iid)):
        print(f"        待办｜{activity} → {who(assignee)}")


def step(
    engine: ProcessEngine, iid: str, title: str, *, nodes=None, tasks=None, state=None
) -> None:
    print(f"  [{title}]")
    expect_position(engine, iid, nodes=nodes, tasks=tasks, state=state, note=title)
    snapshot(engine, iid)


def archived(engine: ProcessEngine, iid: str) -> str:
    row = engine.history.task_history(instance_id=iid)[-1]
    return f"{row.activity_code} · {who(row.assignee)} · {row.outcome}"


def expect_error(title: str, action, *args, **kwargs) -> FloworkError:
    """执行预期失败的动作：打印异常并返回；未抛错即失败。"""
    print(f"  [{title}]")
    try:
        action(*args, **kwargs)
    except FloworkError as exc:
        print(f"    ✗ 预期异常｜{type(exc).__name__}: {exc}")
        return exc
    raise AssertionError(f"{title}：预期抛错但未抛")
