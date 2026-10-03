"""目录竖切测试（M-A 第一刀）：持久化目录的部门树语义与 OrgDirectory 查询面。

对应 docs/product/engine-v1/01-身份与组织模型.md §2 的映射语义：
- "某人的直接主管" = 主属部门的 manager_user_id（部门属性，不是 user 上的 manager 字段）；
- 环检测/删除保护对应 04 需求规格 DIR-01 的验收要点。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowork.directory import DirectoryError, DirectoryStore
from flowork.persistence.db import create_db_engine, file_url, session_factory


@pytest.fixture
def store(tmp_path: Path) -> DirectoryStore:
    engine = create_db_engine(file_url(tmp_path / "directory.db"))
    return DirectoryStore(session_factory(engine))


def seed(store: DirectoryStore) -> None:
    store.create_org_unit("d_all", name="全公司")
    store.create_org_unit("d_eng", name="工程部", parent_code="d_all", manager_user_id="u_bob")
    store.create_user("u_zhang", name="张三", org_unit_code="d_eng")
    store.create_user("u_bob", name="李主管", org_unit_code="d_eng")
    store.create_user("u_ceo", name="王总", org_unit_code="d_all")


class TestManagerResolution:
    def test_manager_of_via_department(self, store: DirectoryStore) -> None:
        seed(store)
        assert store.manager_of("u_zhang") == "u_bob"  # 用户 → 主属部门 → 部门主管
        assert store.department_of("u_zhang") == "d_eng"
        assert store.department_head("d_eng") == "u_bob"
        assert store.parent_department("d_eng") == "d_all"

    def test_manager_of_returns_none_cases(self, store: DirectoryStore) -> None:
        seed(store)
        assert store.manager_of("u_ceo") is None  # 根部门未挂主管
        assert store.manager_of("u_ghost") is None  # 未知用户

    def test_dept_without_manager(self, store: DirectoryStore) -> None:
        store.create_org_unit("d_lab", name="实验室")
        store.create_user("u_new", name="新人", org_unit_code="d_lab")
        assert store.manager_of("u_new") is None

    def test_user_exists_and_departmentless(self, store: DirectoryStore) -> None:
        seed(store)
        store.create_user("u_free", name="无部门")
        assert store.user_exists("u_free")
        assert store.department_of("u_free") is None
        assert not store.user_exists("u_ghost")


class TestTreeIntegrity:
    def test_duplicate_org_unit_rejected(self, store: DirectoryStore) -> None:
        store.create_org_unit("d_all")
        with pytest.raises(DirectoryError):
            store.create_org_unit("d_all")

    def test_duplicate_user_rejected(self, store: DirectoryStore) -> None:
        store.create_user("u_1")
        with pytest.raises(DirectoryError):
            store.create_user("u_1")

    def test_missing_parent_rejected(self, store: DirectoryStore) -> None:
        with pytest.raises(DirectoryError):
            store.create_org_unit("d_x", parent_code="d_missing")

    def test_self_parent_rejected(self, store: DirectoryStore) -> None:
        store.create_org_unit("d_all")
        with pytest.raises(DirectoryError):
            store.create_org_unit("d_sub", parent_code="d_sub")

    def test_move_cycle_rejected(self, store: DirectoryStore) -> None:
        store.create_org_unit("d_all")
        store.create_org_unit("d_a", parent_code="d_all")
        store.create_org_unit("d_b", parent_code="d_a")
        with pytest.raises(DirectoryError):
            store.move_org_unit("d_all", parent_code="d_b")  # 祖先不能挂到子孙下

    def test_delete_protection(self, store: DirectoryStore) -> None:
        seed(store)
        with pytest.raises(DirectoryError):  # 有子部门
            store.delete_org_unit("d_all")
        with pytest.raises(DirectoryError):  # 有在职用户
            store.delete_org_unit("d_eng")
        store.create_org_unit("d_empty", parent_code="d_all")
        store.delete_org_unit("d_empty")  # 空叶子可删


class TestDepartmentQueries:
    def test_users_in_department_recursive(self, store: DirectoryStore) -> None:
        seed(store)
        assert set(store.users_in_department("d_all", recursive=True)) == {
            "u_zhang",
            "u_bob",
            "u_ceo",
        }
        assert set(store.users_in_department("d_all")) == {"u_ceo"}
        assert set(store.users_in_department("d_eng")) == {"u_zhang", "u_bob"}

    def test_suspended_user_excluded(self, store: DirectoryStore) -> None:
        seed(store)
        store.set_user_status("u_zhang", "suspended")
        assert set(store.users_in_department("d_eng")) == {"u_bob"}
        assert store.user_exists("u_zhang")  # 仍存在，仅不参与取人

    def test_like_wildcard_in_code_not_overmatching(self, store: DirectoryStore) -> None:
        store.create_org_unit("d_eng")
        store.create_org_unit("d0eng")  # 兄弟部门：LIKE 未转义 _ 时会被 /d_eng/% 误命中
        store.create_org_unit("d_sub", parent_code="d_eng")
        store.create_user("u_a", org_unit_code="d_eng")
        store.create_user("u_b", org_unit_code="d0eng")
        store.create_user("u_c", org_unit_code="d_sub")
        assert set(store.users_in_department("d_eng", recursive=True)) == {"u_a", "u_c"}
