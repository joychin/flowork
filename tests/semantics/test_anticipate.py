"""P1-9（kernel-v2 spec §11/§16）：anticipate 只读预判测试。

验收：会签中途预判正确（含比例出口）；调用后实例状态零变化。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.node import MultiInstance
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from flowork.persistence.orm import EventOutboxRow, TaskRow


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def deploy_counterset(engine: ProcessEngine) -> None:
    """3 人会签 >=50% 同意过，后面直通结束。"""
    engine.repository.deploy(
        FlowBuilder("cs_flow")
        .start()
        .user_task(
            "sign",
            assignees=["u1", "u2", "u3"],
            multi_instance=MultiInstance(mode="parallel", approve_exit=">=50%"),
        )
        .end()
        .connect("start", "sign")
        .connect("sign", "end")
        .build()
    )


def test_countersign_midway_anticipate_with_ratio_exit(engine: ProcessEngine):
    """会签中途预判（含比例出口）：3 人 ≥50%——第 1 票后预判仍不出，第 2 票后预判出。"""
    deploy_counterset(engine)
    instance_id = engine.runtime.start_process("cs_flow", start_user="u_s")
    tasks = {t.assignee: t for t in engine.task.list_tasks(instance_id=instance_id)}

    first = engine.task.anticipate(tasks["u1"].id, outcome="agree")
    assert (first.node_exit, first.instance_complete) == (False, False)

    engine.task.complete(tasks["u1"].id, actor="u1")
    second = engine.task.anticipate(tasks["u2"].id, outcome="agree")
    # 2/3 ≥ ceil(50%)＝2 → 节点将出，且后继直通 end → 实例将完
    assert (second.node_exit, second.instance_complete) == (True, True)


def test_anticipate_leaves_state_untouched(engine: ProcessEngine):
    """验收：调用后实例状态零变化——不落库、不改计数、不发事件。"""
    deploy_counterset(engine)
    instance_id = engine.runtime.start_process("cs_flow", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]

    with engine.session_factory() as session:
        events_before = len(session.scalars(select(EventOutboxRow)).all())
    token_before = next(t for t in engine.history.tokens(instance_id) if t.activity_code == "sign")

    engine.task.anticipate(task.id, outcome="agree")

    token_after = next(t for t in engine.history.tokens(instance_id) if t.activity_code == "sign")
    assert (token_after.approve_count, token_after.item_count) == (
        token_before.approve_count,
        token_before.item_count,
    )
    with engine.session_factory() as session:
        assert len(session.scalars(select(EventOutboxRow)).all()) == events_before
        assert len(session.scalars(select(TaskRow)).all()) == 3  # 三件待办原样
    assert engine.runtime.get_instance(instance_id).state == "running"


def test_anticipate_matches_actual_outcome(engine: ProcessEngine):
    """预判与实际提交一致：第 2 票真提交后实例完成（与预判 instance_complete 相符）。"""
    deploy_counterset(engine)
    instance_id = engine.runtime.start_process("cs_flow", start_user="u_s")
    tasks = {t.assignee: t for t in engine.task.list_tasks(instance_id=instance_id)}
    engine.task.complete(tasks["u1"].id, actor="u1")
    assert engine.task.anticipate(tasks["u2"].id, outcome="agree").instance_complete is True
    engine.task.complete(tasks["u2"].id, actor="u2")
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_anticipate_reject_routes_to_human_target(engine: ProcessEngine):
    """驳回预判：节点出（回退），落点是人工节点 → 实例未完。"""
    engine.repository.deploy(
        FlowBuilder("rj_flow")
        .start()
        .user_task("fill", assignee="u_f")
        .user_task("mgr", assignee="u_m", reject_target="fill")
        .end()
        .connect("start", "fill")
        .connect("fill", "mgr")
        .connect("mgr", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("rj_flow", start_user="u_s")
    engine.task.complete(
        engine.task.list_tasks(instance_id=instance_id, assignee="u_f")[0].id, actor="u_f"
    )
    mgr = engine.task.list_tasks(instance_id=instance_id, assignee="u_m")[0]
    result = engine.task.anticipate(mgr.id, outcome="reject")
    assert result.node_exit is True
    assert result.instance_complete is False  # 回退到 fill（人工）


def test_anticipate_reject_blocked_until_ratio(engine: ProcessEngine):
    """会签驳回出口 >=2：第 1 票 reject 不出节点。"""
    engine.repository.deploy(
        FlowBuilder("rj_ratio")
        .start()
        .user_task(
            "sign",
            assignees=["u1", "u2", "u3"],
            multi_instance=MultiInstance(
                mode="parallel", approve_exit="all", disapprove_exit=">=2"
            ),
        )
        .end()
        .connect("start", "sign")
        .connect("sign", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("rj_ratio", start_user="u_s")
    tasks = {t.assignee: t for t in engine.task.list_tasks(instance_id=instance_id)}
    assert engine.task.anticipate(tasks["u1"].id, outcome="reject").node_exit is False
    engine.task.reject(tasks["u1"].id, actor="u1")
    assert engine.task.anticipate(tasks["u2"].id, outcome="reject").node_exit is True


def test_anticipate_rejects_unavailable_task(engine: ProcessEngine):
    deploy_counterset(engine)
    instance_id = engine.runtime.start_process("cs_flow", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor=task.assignee)
    with pytest.raises(FloworkError):
        engine.task.anticipate(task.id, outcome="agree")  # 已处理不可预判
    with pytest.raises(ValueError):
        engine.task.anticipate(
            engine.task.list_tasks(instance_id=instance_id)[0].id, outcome="transfer"
        )
