"""数据库装配：engine、会话工厂、建表。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from flowork.persistence.orm import Base


def create_db_engine(url: str) -> Engine:
    """创建数据库引擎（SQLite 默认开启外键与 WAL）。

    内存 SQLite 用 StaticPool：所有连接共享同一个内存库（否则每连接各一份
    空库，跨线程/跨会话不可见——幂等并发等场景的隐性坑）。
    """
    if url.endswith(":memory:") or "mode=memory" in url:
        engine = create_engine(
            url, future=True, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    else:
        engine = create_engine(url, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def file_url(path: str | Path) -> str:
    return f"sqlite:///{Path(path)}"


def url_from_value(value: str | Path) -> str:
    """路径或完整 URL 归一。

    值含 ``://`` 视为完整 SQLAlchemy 连接串原样返回（如
    ``postgresql+psycopg://user:pw@host/db``），否则按 SQLite 文件路径包装。
    ``resolve_db_url`` 与 migrations/env.py 共用本件，契约单源。
    """
    text = str(value)
    if "://" in text:
        return text
    return file_url(text)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


__all__ = [
    "Session",
    "create_db_engine",
    "create_schema",
    "file_url",
    "session_factory",
    "url_from_value",
]
