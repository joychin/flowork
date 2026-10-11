"""M-A 第二刀：角色与范围化授权（DIR-03）。

用例直接对应 docs/product/engine-v1/01-身份与组织模型.md §2 的范围解析规则：
"发起人部门子树 → 逐级向上 → 全局"（最近命中层胜出，同层多人为会签素材）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowork import contrib
from flowork.definitions.definition import load_definition
from flowork.directory import DirectoryError, DirectoryStore
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.persistence.db import create_db_engine, file_url, session_factory


@pytest.fixture
def store(tmp_path: Path) -> DirectoryStore:
    engine = create_db_engine(file_url(tmp_path / "roles.db"))
    store = DirectoryStore(session_factory(engine))
    store.create_org_unit("d_all", name="全公司")
    store.create_org_unit("d_east", name="华东", parent_code="d_all")
    store.create_org_unit("d_sh1", name="上海", parent_code="d_east")
    store.create_org_unit("d_south", name="华南", parent_code="d_all")
    store.create_user("u_zhang", name="张三", org_unit_code="d_sh1")
    store.create_user("u_wang", name="王五", org_unit_code="d_east")
    store.create_user("u_li", name="李四", org_unit_code="d_south")
    store.create_user("u_root", name="root", org_unit_code="d_all")
    store.create_user("u_aud", name="审计员", org_unit_code="d_sh1")
    store.create_user("u_fin2", name="财务二", org_unit_code="d_south")
    store.create_role("finance", name="财务")
    return store


class TestScopedResolution:
    def test_subtree_hit_beats_global(self, store: DirectoryStore) -> None:
        store.assign_role("u_zhang", "finance", org_unit_code="d_east")
        store.assign_role("u_li", "finance")  # 全局
        assert store.users_with_role("finance", within="d_sh1") == ["u_zhang"]
        assert store.users_with_role("finance", within="d_south") == ["u_li"]

    def test_same_level_multiple_holders(self, store: DirectoryStore) -> None:
        store.assign_role("u_zhang", "finance", org_unit_code="d_east")
        store.assign_role("u_wang", "finance", org_unit_code="d_east")
        assert set(store.users_with_role("finance", within="d_sh1")) == {"u_zhang", "u_wang"}

    def test_nearest_level_wins(self, store: DirectoryStore) -> None:
        store.assign_role("u_zhang", "finance", org_unit_code="d_east")
        store.assign_role("u_root", "finance", org_unit_code="d_all")
        assert store.users_with_role("finance", within="d_sh1") == ["u_zhang"]  # d_east 更近
        assert store.users_with_role("finance", within="d_south") == ["u_root"]  # 链上 d_all 命中

    def test_no_context_returns_all(self, store: DirectoryStore) -> None:
        store.assign_role("u_zhang", "finance", org_unit_code="d_east")
        store.assign_role("u_li", "finance")
        assert set(store.users_with_role("finance")) == {"u_zhang", "u_li"}

    def test_unknown_role_returns_empty(self, store: DirectoryStore) -> None:
        assert store.users_with_role("nope", within="d_sh1") == []

    def test_role_default_scope_inherited(self, store: DirectoryStore) -> None:
        store.create_role("auditor", name="审计", default_scope_org_unit="d_east")
        store.assign_role("u_aud", "auditor")  # 未显式给范围 → 继承角色默认
        assert store.users_with_role("auditor", within="d_sh1") == ["u_aud"]
        assert store.users_with_role("auditor", within="d_south") == []


class TestAssignmentIntegrity:
    def test_duplicate_assignment_rejected(self, store: DirectoryStore) -> None:
        store.assign_role("u_zhang", "finance", org_unit_code="d_east")
        with pytest.raises(DirectoryError):
            store.assign_role("u_zhang", "finance", org_unit_code="d_east")  # 同有效范围重复
        store.assign_role("u_zhang", "finance", org_unit_code="d_south")  # 不同范围可并存
        store.assign_role("u_zhang", "finance")  # 全局亦可并存（有效范围不同）
        assert store.users_with_role("finance", within="d_sh1") == ["u_zhang"]
        assert store.users_with_role("finance", within="d_south") == ["u_zhang"]  # 最近命中 d_south

    def test_unknown_refs_rejected(self, store: DirectoryStore) -> None:
        with pytest.raises(DirectoryError):
            store.assign_role("u_ghost", "finance")
        with pytest.raises(DirectoryError):
            store.assign_role("u_zhang", "ghost_role")
        with pytest.raises(DirectoryError):
            store.assign_role("u_zhang", "finance", org_unit_code="d_ghost")

    def test_duplicate_role_rejected(self, store: DirectoryStore) -> None:
        with pytest.raises(DirectoryError):
            store.create_role("finance")


DEFINITION = {
    "code": "role_vertical",
    "name": "角色竖切（财务审批）",
    "nodes": [
        {"code": "start", "name": "发起", "kind": "start"},
        {
            "code": "fin",
            "name": "财务审批",
            "kind": "user_task",
            "assignee": 'users_with_role("finance")',
            "multi_instance": {"mode": "parallel"},
        },
        {"code": "end", "name": "结束", "kind": "end"},
    ],
    "flows": [
        {"source": "start", "target": "fin"},
        {"source": "fin", "target": "end"},
    ],
}


class TestRoleEndToEnd:
    def test_role_task_follows_requester_scope(self, tmp_path: Path) -> None:
        config = EngineConfig(db_file=tmp_path / "e2e.db")
        engine = ProcessEngine(config)
        store = DirectoryStore(engine.session_factory)
        store.create_org_unit("d_all")
        store.create_org_unit("d_east", parent_code="d_all")
        store.create_org_unit("d_sh1", parent_code="d_east")
        store.create_org_unit("d_south", parent_code="d_all")
        store.create_user("u_zhang", org_unit_code="d_sh1")
        store.create_user("u_east_fin", org_unit_code="d_east")
        store.create_user("u_li", org_unit_code="d_south")
        store.create_user("u_global_fin", org_unit_code="d_south")
        store.create_role("finance")
        store.assign_role("u_east_fin", "finance", org_unit_code="d_east")
        store.assign_role("u_global_fin", "finance")
        config.org = store
        config.expression_functions = contrib.cel_vocabulary(config.org)  # 位点统一：CEL 词汇

        definition = load_definition(DEFINITION, engine.config.node_registry)
        engine.repository.deploy(definition)

        east = engine.runtime.start_process("role_vertical", start_user="u_zhang")
        assert len(engine.task.list_tasks(instance_id=east, assignee="u_east_fin")) == 1

        south = engine.runtime.start_process("role_vertical", start_user="u_li")
        assert len(engine.task.list_tasks(instance_id=south, assignee="u_global_fin")) == 1


def test_users_with_role_excludes_suspended(store):
    """回归（code-review 2026-10 P2-22）：role: 组解析过滤停用用户——与 org:
    侧同口径。修复前停用员工经角色候选仍可被解析（两路口径不一致）。"""
    store.assign_role("u_zhang", "finance")
    store.set_user_status("u_zhang", "suspended", acted_by="u_admin")
    assert store.users_with_role("finance") == [], "停用用户不进角色组"
    store.set_user_status("u_zhang", "active", acted_by="u_admin")
    assert store.users_with_role("finance") == ["u_zhang"], "复效后恢复"
