"""M-A 收口批：委托（org_delegation）/ admins / 写审计 / 读缓存。

委托语义（engine-v1 01 §5）：[since, until] 窗口内待办转受托人，resolver
单跳替换；撤销留痕。admins=平台角色 admin 持有人（02-权限模型 §3）。
写审计（org_directory_audit 追加表）与读缓存（写后全清）同批。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from flowork.clock import utc_now
from flowork.directory import DirectoryAuditRow, DirectoryError, DirectoryStore
from flowork.persistence.db import create_db_engine, file_url, session_factory


@pytest.fixture
def store(tmp_path: Path) -> DirectoryStore:
    engine = create_db_engine(file_url(tmp_path / "delegation.db"))
    store = DirectoryStore(session_factory(engine))
    store.create_org_unit("d_all", name="全公司")
    store.create_user("u_boss", name="领导", org_unit_code="d_all")
    store.create_user("u_sub", name="下属", org_unit_code="d_all")
    store.create_user("u_other", name="路人", org_unit_code="d_all")
    return store


def _audit_rows(store: DirectoryStore) -> list[DirectoryAuditRow]:
    with store._factory() as session:
        return list(session.scalars(select(DirectoryAuditRow).order_by(DirectoryAuditRow.id)).all())


# —— 委托窗口 ——


def test_delegation_active_inside_window(store: DirectoryStore):
    now = utc_now()
    store.create_delegation(
        "u_boss", "u_sub", since=now - timedelta(hours=1), until=now + timedelta(days=7)
    )
    assert store.delegation_of("u_boss") == "u_sub"
    # 显式时刻：窗口前后各查一次
    assert store.delegation_of("u_boss", now + timedelta(days=8)) is None
    assert store.delegation_of("u_boss", now - timedelta(hours=2)) is None


def test_delegation_open_ended_window(store: DirectoryStore):
    """since/until 缺省＝该侧无限。"""
    store.create_delegation("u_boss", "u_sub")
    assert store.delegation_of("u_boss") == "u_sub"
    assert store.delegation_of("u_boss", utc_now() + timedelta(days=365)) == "u_sub"


def test_delegation_revoked_stops_replacement(store: DirectoryStore):
    store.create_delegation("u_boss", "u_sub")
    store.revoke_delegation("u_boss", "u_sub")
    assert store.delegation_of("u_boss") is None
    infos = store.delegations_of("u_boss")
    assert len(infos) == 1 and infos[0].revoked  # 撤销留痕（delegations_of 查询面）


def test_delegation_rejects_self_and_bad_window(store: DirectoryStore):
    with pytest.raises(DirectoryError):
        store.create_delegation("u_boss", "u_boss")
    now = utc_now()
    with pytest.raises(DirectoryError):
        store.create_delegation("u_boss", "u_sub", since=now + timedelta(days=1), until=now)


def test_delegation_latest_wins(store: DirectoryStore):
    store.create_delegation("u_boss", "u_sub")
    store.create_delegation("u_boss", "u_other")
    assert store.delegation_of("u_boss") == "u_other"  # 同向多条：最新一条胜


# —— resolver 集成：委托替换真正生效（单跳不递归） ——


def test_resolver_replaces_delegated_assignee(store: DirectoryStore, tmp_path: Path):
    from flowork.definitions.builder import FlowBuilder
    from flowork.engine.config import EngineConfig
    from flowork.engine.engine import ProcessEngine

    store.create_delegation("u_boss", "u_sub")
    engine = ProcessEngine(EngineConfig(db_file=tmp_path / "wf.db", org=store))
    definition = (
        FlowBuilder("delegated")
        .start()
        .user_task("approve", assignee="'u_boss'")
        .end()
        .connect("start", "approve")
        .connect("approve", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("delegated", start_user="u_maker")
    tasks = engine.task.list_tasks(instance_id=iid)
    assert [t.assignee for t in tasks] == ["u_sub"]  # 委托替换生效（原 u_boss → u_sub）


# —— admins＝平台角色 admin 持有人 ——


def test_admins_from_admin_role(store: DirectoryStore):
    assert store.admins == []  # 无 admin 角色/授权时为空
    store.create_role("admin", name="平台管理员")
    store.assign_role("u_boss", "admin", acted_by="u_root")
    assert store.admins == ["u_boss"]


# —— 写审计 ——


def test_write_audit_records_actor_and_entity(store: DirectoryStore):
    store.create_role("finance", acted_by="u_root")
    store.assign_role("u_boss", "finance", acted_by="u_root")
    store.create_delegation("u_boss", "u_sub", acted_by="u_root")
    rows = _audit_rows(store)
    actions = [(r.action, r.entity, r.acted_by) for r in rows]
    # 部门/用户建档（fixture）+ 三笔显式写全部留痕，actor 归因可查
    assert ("role.create", "role:finance", "u_root") in actions
    assert ("role.assign", "role:finance", "u_root") in actions
    assert ("delegation.create", "user:u_boss", "u_root") in actions
    assign = next(r for r in rows if r.action == "role.assign")
    assert assign.detail["user"] == "u_boss"


# —— 读缓存：写后全清，改角色立即可见 ——


def test_read_cache_invalidated_on_write(store: DirectoryStore):
    store.create_role("finance")
    assert store.subjects_in("role:finance") == []  # 首读入缓存
    store.assign_role("u_sub", "finance")
    assert store.subjects_in("role:finance") == ["u_sub"]  # 写后缓存失效，立即可见
    assert store.is_member("u_sub", "role:finance") is True
