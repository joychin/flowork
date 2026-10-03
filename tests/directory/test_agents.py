"""M-A 第三刀：Agent 一等身份（DIR-05）。

对应 docs/product/engine-v1/01-身份与组织模型.md §3 Agent 表与 04 规格 DIR-05：
注册（owner/部门/on_behalf_of）、状态机 trial→active→suspended、一次性凭证、
停用即时失效。实例归因（on_behalf_of → started_by 双链）随 M-B 的 RUN-01 落地。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowork.directory import DirectoryError, DirectoryStore
from flowork.persistence.db import create_db_engine, file_url, session_factory


@pytest.fixture
def store(tmp_path: Path) -> DirectoryStore:
    engine = create_db_engine(file_url(tmp_path / "agents.db"))
    store = DirectoryStore(session_factory(engine))
    store.create_org_unit("d_all")
    store.create_org_unit("d_eng", parent_code="d_all")
    store.create_user("u_boss", name="负责人", org_unit_code="d_eng")
    store.create_user("u_zhang", name="张三", org_unit_code="d_eng")
    return store


class TestRegister:
    def test_register_defaults_to_trial(self, store: DirectoryStore) -> None:
        info = store.register_agent(
            "ag_cs", name="客服Agent", owner_user_id="u_boss", org_unit_code="d_eng"
        )
        assert info.status == "trial"
        assert info.owner_user_id == "u_boss"
        assert info.org_unit_code == "d_eng"
        assert info.on_behalf_of_user is None
        assert store.agent_info("ag_cs") == info

    def test_on_behalf_of_recorded(self, store: DirectoryStore) -> None:
        info = store.register_agent(
            "ag_rep", name="外呼Agent", owner_user_id="u_boss", on_behalf_of_user="u_zhang"
        )
        assert info.on_behalf_of_user == "u_zhang"
        assert store.agent_owner("ag_rep") == "u_boss"

    def test_unknown_refs_rejected(self, store: DirectoryStore) -> None:
        with pytest.raises(DirectoryError):
            store.register_agent("ag_x", owner_user_id="u_ghost")
        with pytest.raises(DirectoryError):
            store.register_agent("ag_x", owner_user_id="u_boss", org_unit_code="d_ghost")
        with pytest.raises(DirectoryError):
            store.register_agent("ag_x", owner_user_id="u_boss", on_behalf_of_user="u_ghost")

    def test_duplicate_agent_rejected(self, store: DirectoryStore) -> None:
        store.register_agent("ag_cs", owner_user_id="u_boss")
        with pytest.raises(DirectoryError):
            store.register_agent("ag_cs", owner_user_id="u_boss")

    def test_agent_owner_unknown_returns_none(self, store: DirectoryStore) -> None:
        assert store.agent_owner("ag_ghost") is None


class TestStatusMachine:
    def test_transitions(self, store: DirectoryStore) -> None:
        store.register_agent("ag_cs", owner_user_id="u_boss")
        store.activate_agent("ag_cs")
        assert store.agent_info("ag_cs").status == "active"
        store.suspend_agent("ag_cs")
        assert store.agent_info("ag_cs").status == "suspended"
        store.resume_agent("ag_cs")
        assert store.agent_info("ag_cs").status == "active"

    def test_invalid_transitions_rejected(self, store: DirectoryStore) -> None:
        store.register_agent("ag_cs", owner_user_id="u_boss")
        store.activate_agent("ag_cs")
        with pytest.raises(DirectoryError):  # active 不能再 activate
            store.activate_agent("ag_cs")
        store.suspend_agent("ag_cs")
        with pytest.raises(DirectoryError):  # suspended 不能 suspend
            store.suspend_agent("ag_cs")

    def test_unknown_agent_rejected(self, store: DirectoryStore) -> None:
        with pytest.raises(DirectoryError):
            store.activate_agent("ag_ghost")


class TestCredentials:
    def test_issue_verify_roundtrip(self, store: DirectoryStore) -> None:
        store.register_agent("ag_cs", owner_user_id="u_boss")
        token = store.issue_credential("ag_cs")
        assert token and token.startswith("fwk-ag-")
        assert store.verify_credential(token) == "ag_cs"
        assert store.verify_credential("fwk-ag-bogus-deadbeef") is None

    def test_multiple_credentials_allowed(self, store: DirectoryStore) -> None:
        store.register_agent("ag_cs", owner_user_id="u_boss")
        first = store.issue_credential("ag_cs")
        second = store.issue_credential("ag_cs")
        assert first != second
        assert store.verify_credential(first) == "ag_cs"
        assert store.verify_credential(second) == "ag_cs"

    def test_revoke_immediately_invalid(self, store: DirectoryStore) -> None:
        store.register_agent("ag_cs", owner_user_id="u_boss")
        token = store.issue_credential("ag_cs")
        store.revoke_credential(token)
        assert store.verify_credential(token) is None

    def test_suspend_immediately_invalidates(self, store: DirectoryStore) -> None:
        """US-104 GWT②：停用即时生效——verify 每次读当前状态，无缓存窗口。"""
        store.register_agent("ag_cs", owner_user_id="u_boss")
        store.activate_agent("ag_cs")
        token = store.issue_credential("ag_cs")
        assert store.verify_credential(token) == "ag_cs"
        store.suspend_agent("ag_cs")
        assert store.verify_credential(token) is None
        with pytest.raises(DirectoryError):  # 停用后也不可再签发
            store.issue_credential("ag_cs")
        store.resume_agent("ag_cs")
        assert store.verify_credential(token) == "ag_cs"  # 恢复后原凭证仍有效

    def test_unknown_agent_issue_rejected(self, store: DirectoryStore) -> None:
        with pytest.raises(DirectoryError):
            store.issue_credential("ag_ghost")
