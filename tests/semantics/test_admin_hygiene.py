"""管理员手术终态守卫 + 候选身份关联清理（全仓 review 2026-10-13 批 3 P2 组）。

- activate_node/cancel_node 修前无终态守卫：completed 实例上激活出僵尸令牌
  与 ready 任务（状态与事件面背离）、发出无人对应的取消事件——对齐同文件
  finish_instance 已有守卫（paused/suspended 按 D2 管理手术口径放行）；
- 候选身份关联（wf_identity_link）修前只在 remove_instance 清——取消/办结
  路径留孤儿行（查询 join 活表不受影响，纯卫生）。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from flowork.persistence.orm import IdentityLinkRow


def _candidate_engine() -> ProcessEngine:
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("hyg")
        .start()
        .user_task("a", candidate_users=["'u1'"])
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    return engine


def _identity_count(engine: ProcessEngine, task_id: str) -> int:
    def _cmd(ctx):
        from sqlalchemy import select

        return len(
            list(
                ctx.session.scalars(
                    select(IdentityLinkRow).where(IdentityLinkRow.task_id == task_id)
                )
            )
        )

    result: int = engine.execute(_cmd)
    return result


def _completed_instance() -> tuple[ProcessEngine, str]:
    engine = _candidate_engine()
    instance_id = engine.runtime.start_process("hyg", start_user="u1")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.claim(task.id, "u1")
    engine.task.complete(task.id, actor="u1")
    assert engine.runtime.get_instance(instance_id).state == "completed"
    return engine, instance_id


def test_activate_node_refuses_terminal_instance():
    engine, instance_id = _completed_instance()
    with pytest.raises(FloworkError, match="不可激活节点"):
        engine.task.activate_node(instance_id, "a", actor="admin")
    assert engine.runtime.get_instance(instance_id).state == "completed"
    assert engine.task.list_tasks(instance_id=instance_id) == []


def test_cancel_node_refuses_terminal_instance():
    engine, instance_id = _completed_instance()
    with pytest.raises(FloworkError, match="不可取消节点"):
        engine.task.cancel_node(instance_id, "a", actor="admin")


def test_identity_link_cleaned_on_complete():
    engine = _candidate_engine()
    instance_id = engine.runtime.start_process("hyg", start_user="u1")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.claim(task.id, "u1")
    assert _identity_count(engine, task.id) > 0  # 候选/认领关联在
    engine.task.complete(task.id, actor="u1")
    assert _identity_count(engine, task.id) == 0  # 办结即清


def test_identity_link_cleaned_on_cancel():
    engine = _candidate_engine()
    instance_id = engine.runtime.start_process("hyg", start_user="u1")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.claim(task.id, "u1")
    engine.runtime.cancel_process(instance_id, actor="admin", reason="取消")
    assert _identity_count(engine, task.id) == 0  # 取消即清
