"""P1-7（kernel-v2 spec §5.6/§16）：身份快照 actor_context 测试。

验收：完成事务内写入（组织变更不影响已定格内容）；默认空实现裸跑不受影响；
4KB 超限拒收；历史/查询带出。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.participants.org_directory import OrgDirectory


def _flow(code: str = "snap"):
    return (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_zhang'")
        .user_task("b", assignee="'u_bob'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )


def _history_of(engine: ProcessEngine, instance_id: str, activity: str):
    return next(h for h in engine.history.task_history(instance_id) if h.activity_code == activity)


def test_snapshot_written_in_completion_transaction():
    """完成事务内定格：事后组织变更不影响已存内容（审计的「他当时是谁」）。"""
    org = OrgDirectory.from_dict(
        {
            "users": {
                "u_zhang": {"manager": "u_bob", "department": "d_eng"},
                "u_bob": {"manager": "u_ceo", "department": "d_eng", "roles": ["eng_lead"]},
            },
            "departments": {"d_eng": {"head": "u_bob"}},
        }
    )
    engine = ProcessEngine(EngineConfig(org=org))
    engine.repository.deploy(_flow())
    instance_id = engine.runtime.start_process("snap", start_user="u_ceo")

    task = engine.task.list_tasks(instance_id=instance_id, assignee="u_zhang")[0]
    engine.task.complete(task.id, actor="u_zhang")
    history = _history_of(engine, instance_id, "a")
    assert history.actor_context == {"department": "d_eng", "roles": []}

    # 组织变更（调岗）：已定格的历史不变，下一个办理人定格新事实
    org.users["u_bob"]["department"] = "d_fin"
    task_b = engine.task.list_tasks(instance_id=instance_id, assignee="u_bob")[0]
    engine.task.complete(task_b.id, actor="u_bob")
    history_b = _history_of(engine, instance_id, "b")
    assert history_b.actor_context["department"] == "d_fin"


def test_bare_engine_without_org_gets_empty_context():
    """默认空实现：未配置目录 → {}，裸跑/单测不受影响。"""
    engine = ProcessEngine()
    engine.repository.deploy(_flow("snap_bare"))
    instance_id = engine.runtime.start_process("snap_bare", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_zhang")
    assert _history_of(engine, instance_id, "a").actor_context == {}


def test_oversized_snapshot_rejected():
    """4KB 上限拒收：宿主 snapshot 返回过大内容 → 完成被拒（不入库）。"""

    class FatDirectory(OrgDirectory):
        def snapshot(self, subject: str) -> dict:
            return {"blob": "x" * 8192}

    engine = ProcessEngine(EngineConfig(org=FatDirectory()))
    engine.repository.deploy(_flow("snap_fat"))
    instance_id = engine.runtime.start_process("snap_fat", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    with pytest.raises(Exception, match="身份快照超限"):
        engine.task.complete(task.id, actor="u_zhang")
    # 完成被拒：任务仍在活表，历史无行
    assert engine.task.list_tasks(instance_id=instance_id, assignee="u_zhang")
    assert engine.history.task_history(instance_id) == []
