"""目录竖切端到端：持久化目录注入引擎后，manager_of() 取人函数按"主属部门主管"落任务。

验证 docs/product/engine-v1/05-架构总体设计.md §4 的关键接缝：
目录（新模块）→ EngineConfig.org → kernel.resolve_participants → 任务落库。
"""

from __future__ import annotations

from pathlib import Path

from flowork import contrib
from flowork.definitions.definition import load_definition
from flowork.directory import DirectoryStore
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine

DEFINITION = {
    "code": "dir_vertical",
    "name": "目录竖切（主管审批）",
    "nodes": [
        {"code": "start", "name": "发起", "kind": "start"},
        {"code": "mgr", "name": "主管审批", "kind": "user_task", "assignee": "manager_of()"},
        {"code": "end", "name": "结束", "kind": "end"},
    ],
    "flows": [
        {"source": "start", "target": "mgr"},
        {"source": "mgr", "target": "end"},
    ],
}


def make_engine_with_directory(tmp_path: Path) -> tuple[ProcessEngine, DirectoryStore]:
    config = EngineConfig(db_file=tmp_path / "vertical.db")
    engine = ProcessEngine(config)
    store = DirectoryStore(engine.session_factory)
    store.create_org_unit("d_all", name="全公司")
    store.create_org_unit("d_eng", name="工程部", parent_code="d_all", manager_user_id="u_bob")
    store.create_user("u_zhang", name="张三", org_unit_code="d_eng")
    store.create_user("u_bob", name="李主管", org_unit_code="d_eng")
    config.org = store
    config.participant_functions = contrib.org_vocabulary()  # v2.1：词汇由宿主挂
    definition = load_definition(DEFINITION, engine.config.node_registry)
    engine.repository.save_definition(definition)
    engine.repository.publish("dir_vertical")
    return engine, store


def test_manager_task_lands_on_department_manager(tmp_path: Path) -> None:
    engine, _store = make_engine_with_directory(tmp_path)
    instance_id = engine.runtime.start_process("dir_vertical", start_user="u_zhang")

    tasks = engine.task.list_tasks(instance_id=instance_id, assignee="u_bob")
    assert len(tasks) == 1, "任务应落到发起人主属部门的主管"
    assert tasks[0].assignee == "u_bob"

    engine.task.complete(tasks[0].id, actor="u_bob")
    assert engine.task.list_tasks(instance_id=instance_id) == []


def test_reshuffle_manager_reroutes_new_instances(tmp_path: Path) -> None:
    """主管换人只影响新实例（在途按创建时解析，任务不迁移）——目录变更语义。"""
    engine, store = make_engine_with_directory(tmp_path)
    first = engine.runtime.start_process("dir_vertical", start_user="u_zhang")
    assert len(engine.task.list_tasks(instance_id=first, assignee="u_bob")) == 1

    store.set_org_unit_manager("d_eng", manager_user_id="u_new_boss")
    store.create_user("u_new_boss", name="新主管", org_unit_code="d_eng")

    second = engine.runtime.start_process("dir_vertical", start_user="u_zhang")
    assert len(engine.task.list_tasks(instance_id=second, assignee="u_new_boss")) == 1
    assert len(engine.task.list_tasks(instance_id=first, assignee="u_bob")) == 1  # 在途不动
