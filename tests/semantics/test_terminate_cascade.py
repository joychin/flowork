"""终止/取消子树级联（2026-10-09 第十四轮，spec §7）。

来源：收口盘点 + tests/suite/test_construct_subprocess.py 曾钉住的开放问题
「父 terminate 不级联子实例（孤儿运行）」——A9 多实例子流程后面变大：
孤儿待办是用户可见 bug，对齐 Camunda/Zeebe 子树终止后收口。
三入口全覆盖：terminate_process / cancel_process / 实例时限策略。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.persistence.orm import EventOutboxRow


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, 9, 0, 0)

    def __call__(self) -> datetime:
        return self.value

    def advance_minutes(self, minutes: int) -> None:
        self.value += timedelta(minutes=minutes)


@pytest.fixture
def engine() -> ProcessEngine:
    clock = FakeClock()
    return ProcessEngine(EngineConfig(now=clock))


def _deploy_nested(engine: ProcessEngine, *, with_deadline: str | None = None) -> None:
    """孙→子→父 三层嵌套（grand_child 由 child 的 call_activity 启动）。"""
    engine.repository.deploy(
        FlowBuilder("grand_def")
        .start()
        .user_task("g_work", assignee="'u_g'")
        .end()
        .connect("start", "g_work")
        .connect("g_work", "end")
        .build()
    )
    engine.repository.deploy(
        FlowBuilder("child_def")
        .start()
        .call_activity("call_grand", process_code="grand_def")
        .end()
        .connect("start", "call_grand")
        .connect("call_grand", "end")
        .build()
    )
    parent = (
        FlowBuilder("parent_def")
        .start()
        .call_activity("call_child", process_code="child_def")
        .end()
        .connect("start", "call_child")
        .connect("call_child", "end")
        .build()
    )
    if with_deadline:
        parent.deadline = {"after_minutes": 30, "strategy": with_deadline}
    engine.repository.deploy(parent)


def _children_of(engine: ProcessEngine, parent_id: str) -> list:
    return [i for i in engine.history.instances() if i.parent_id == parent_id]


def _events_of(engine: ProcessEngine, instance_id: str) -> list[tuple[str, dict]]:
    with engine.session_factory() as session:
        rows = (
            session.query(EventOutboxRow)
            .filter(EventOutboxRow.instance_id == instance_id)
            .order_by(EventOutboxRow.id)
            .all()
        )
        return [(r.event_name, dict(r.payload or {})) for r in rows]


def _drive_to_grand(engine: ProcessEngine) -> tuple[str, str, str]:
    """发起父流程并推进到孙实例运行中，返回 (parent, child, grand) 实例 id。"""
    parent = engine.runtime.start_process("parent_def", start_user="u_s")
    child = _children_of(engine, parent)[0].id
    grand = _children_of(engine, child)[0].id
    return parent, child, grand


def test_terminate_cascades_whole_tree(engine: ProcessEngine):
    """父 terminate → 子/孙（运行中）整树 terminated，待办撤销留痕。"""
    _deploy_nested(engine)
    parent, child, grand = _drive_to_grand(engine)
    assert engine.runtime.get_instance(grand).state == "running"

    engine.runtime.terminate_process(parent, actor="admin", reason="砍树")
    assert engine.runtime.get_instance(parent).state == "terminated"
    assert engine.runtime.get_instance(child).state == "terminated"
    assert engine.runtime.get_instance(grand).state == "terminated"
    # 孙的待办撤销进历史（outcome=cancelled），不残留活任务
    assert engine.task.list_tasks(instance_id=grand) == []
    # 父事件携带级联清单；子事件可溯源（cascade_from）
    parent_events = _events_of(engine, parent)
    assert any(
        name == "process_terminated" and set(p.get("cascaded_instances", [])) == {child, grand}
        for name, p in parent_events
    )
    child_events = _events_of(engine, child)
    assert any(
        name == "process_terminated" and p.get("cascade_from") == parent for name, p in child_events
    )


def test_cancel_cascades_whole_tree(engine: ProcessEngine):
    """cancel 同样级联（终态 cancelled）。"""
    _deploy_nested(engine)
    parent, child, grand = _drive_to_grand(engine)

    engine.runtime.cancel_process(parent, actor="admin", reason="作废")
    assert engine.runtime.get_instance(parent).state == "cancelled"
    assert engine.runtime.get_instance(child).state == "cancelled"
    assert engine.runtime.get_instance(grand).state == "cancelled"


def test_terminate_cascades_into_paused_child(engine: ProcessEngine):
    """暂停中的子随树终态（管理员砍树≠MI 提前收工，暂停不豁免）。"""
    _deploy_nested(engine)
    parent, child, _grand = _drive_to_grand(engine)
    engine.runtime.pause_process(child, actor="admin", reason="子流程暂停")

    engine.runtime.terminate_process(parent, actor="admin", reason="砍树")
    assert engine.runtime.get_instance(child).state == "terminated"
    assert engine.runtime.get_instance(parent).state == "terminated"


def test_terminate_keeps_settled_children(engine: ProcessEngine):
    """已结算子不动：MI 两个子，一完一跑，砍树后完成的保持 completed。"""
    engine.repository.deploy(
        FlowBuilder("leaf_def")
        .start()
        .user_task("w", assignee="'u_worker'")
        .end()
        .connect("start", "w")
        .connect("w", "end")
        .build()
    )
    engine.repository.deploy(
        FlowBuilder("mi_parent")
        .start()
        .call_activity(
            "fan",
            process_code="leaf_def",
            multi_instance={"subjects": ["'u_a'", "'u_b'"]},
        )
        .end()
        .connect("start", "fan")
        .connect("fan", "end")
        .build()
    )
    parent = engine.runtime.start_process("mi_parent", start_user="u_s")
    children = _children_of(engine, parent)
    assert len(children) == 2

    done, running = children[0], children[1]
    task = engine.task.list_tasks(instance_id=done.id)[0]
    engine.task.complete(task.id, actor="u_worker")
    assert engine.runtime.get_instance(done.id).state == "completed"

    engine.runtime.terminate_process(parent, actor="admin", reason="砍树")
    assert engine.runtime.get_instance(done.id).state == "completed"  # 已结算不动
    assert engine.runtime.get_instance(running.id).state == "terminated"
    assert engine.runtime.get_instance(parent).state == "terminated"


def test_instance_deadline_strategy_cascades(engine: ProcessEngine):
    """实例时限策略（terminate/cancel）与 runtime 路径同款级联。"""
    for strategy, expected in (("terminate", "terminated"), ("cancel", "cancelled")):
        clock = FakeClock()
        eng = ProcessEngine(EngineConfig(now=clock))
        _deploy_nested(eng, with_deadline=strategy)
        parent, child, _grand = _drive_to_grand(eng)

        clock.advance_minutes(31)
        eng.jobs.tick()
        assert eng.runtime.get_instance(parent).state == expected
        assert eng.runtime.get_instance(child).state == expected


def test_terminate_without_children_reports_empty_cascade(engine: ProcessEngine):
    """无子实例：级联清单为空，行为与旧 terminate 一致。"""
    engine.repository.deploy(
        FlowBuilder("solo_def")
        .start()
        .user_task("w", assignee="'u_w'")
        .end()
        .connect("start", "w")
        .connect("w", "end")
        .build()
    )
    iid = engine.runtime.start_process("solo_def", start_user="u_s")
    engine.runtime.terminate_process(iid, actor="admin", reason="无子")
    assert engine.runtime.get_instance(iid).state == "terminated"
    assert any(
        name == "process_terminated" and p.get("cascaded_instances") == []
        for name, p in _events_of(engine, iid)
    )
