"""fixture 自检：世界定义全部过设计时校验、组织世界无孤儿、双引擎可装配。"""

from __future__ import annotations

from flowork.definitions.validator import assert_valid
from flowork.runtime.registry import create_registry
from tests.suite.world_catalog import (
    BUILDERS,
    STATIC_WORLD_CODES,
    STORE_WORLD_CODES,
    build_definition,
)
from tests.suite.world_org import (
    ORG_UNITS,
    ROLE_ASSIGNMENTS,
    USERS,
    build_directory_store,
    build_static_org,
)


def test_all_world_definitions_pass_design_validation() -> None:
    registry = create_registry()
    for code in BUILDERS:
        assert_valid(build_definition(code), registry)


def test_store_world_has_no_orphans(tmp_path) -> None:
    store = build_directory_store(tmp_path / "sanity.db")
    unit_codes = {code for code, _n, _p, _m in ORG_UNITS}
    user_ids = {user for user, _n, _d in USERS}
    for _code, _name, parent, manager in ORG_UNITS:
        assert parent is None or parent in unit_codes
        assert manager is not None and manager in user_ids
    for _user, _name, dept in USERS:
        assert dept in unit_codes
        assert store.user_exists(_user)
    for user, role, _scope in ROLE_ASSIGNMENTS:
        assert user in user_ids
        assert store.users_with_role(role)  # 角色至少有一名持有者


def test_static_world_agrees_with_store(tmp_path) -> None:
    """两种物化的引擎必需四方法口径一致（manager_of/subjects_in/snapshot 部门事实）。"""
    store = build_directory_store(tmp_path / "agree.db")
    static = build_static_org()
    for user_id, _name, _dept in USERS:
        assert store.manager_of(user_id) == static.manager_of(user_id), user_id
    assert store.subjects_in("role:finance_approver") == static.subjects_in("role:finance_approver")
    assert static.delegation_of("u_lead_fe") == "u_lead_be"  # 仅静态目录支持委托
    assert static.admins == ["u_ceo"]


def test_suite_engine_has_store_world_deployed(suite_engine) -> None:
    for code in STORE_WORLD_CODES:
        assert suite_engine.repository.get_definition(code) is not None, code


def test_static_engine_has_static_world_deployed(static_engine) -> None:
    for code in STATIC_WORLD_CODES:
        assert static_engine.repository.get_definition(code) is not None, code
