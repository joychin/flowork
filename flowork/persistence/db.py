"""数据库装配：engine、会话工厂、建表。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from flowork.persistence.orm import Base


def create_db_engine(url: str) -> Engine:
    """创建数据库引擎（SQLite 默认开启外键与 WAL）。"""
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


def memory_url() -> str:
    return "sqlite:///:memory:"


def file_url(path: str | Path) -> str:
    return f"sqlite:///{Path(path)}"


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


__all__ = [
    "Session",
    "create_db_engine",
    "create_schema",
    "file_url",
    "memory_url",
    "session_factory",
]
