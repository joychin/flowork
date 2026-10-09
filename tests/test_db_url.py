"""db_file / FLOWORK_DB 的 URL 通道（spec §14 支持矩阵：SQLite 路径或数据库 URL）。

来源：PG 冒烟批——db_file 此前恒按 SQLite 路径包装，``postgresql+psycopg://``
会被拼成畸形串；本测试钉住「含 :// 即完整 URL 原样透传」契约。归一逻辑
单源在 ``persistence.db.url_from_value``，resolve_db_url 与 migrations/env.py
共用它（env.py 的接线由 tests/pg 的 alembic 冒烟端到端验证）。
"""

from __future__ import annotations

from pathlib import Path

from flowork.engine.config import EngineConfig, resolve_db_url
from flowork.persistence.db import url_from_value


def test_plain_path_stays_sqlite_file() -> None:
    assert url_from_value("flowork.db") == "sqlite:///flowork.db"


def test_pathlib_path_stays_sqlite_file(tmp_path: Path) -> None:
    target = tmp_path / "designer.db"
    assert url_from_value(target) == f"sqlite:///{target}"


def test_full_url_passes_through_untouched() -> None:
    dsn = "postgresql+psycopg://user:pw@localhost:5432/flowork"
    assert url_from_value(dsn) == dsn


def test_resolve_db_url_plain_path() -> None:
    cfg = EngineConfig(db_file="flowork.db")
    assert resolve_db_url(cfg) == "sqlite:///flowork.db"


def test_resolve_db_url_full_url() -> None:
    dsn = "postgresql+psycopg://user:pw@localhost:5432/flowork"
    cfg = EngineConfig(db_file=dsn)
    assert resolve_db_url(cfg) == dsn


def test_resolve_db_url_uses_db_url_when_db_file_absent() -> None:
    cfg = EngineConfig(db_url="postgresql+psycopg://localhost/flowork")
    assert resolve_db_url(cfg) == "postgresql+psycopg://localhost/flowork"


def test_resolve_db_url_default_memory() -> None:
    assert resolve_db_url(EngineConfig()) == "sqlite:///:memory:"
