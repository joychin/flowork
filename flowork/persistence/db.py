"""数据库装配：engine、会话工厂、建表。"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from flowork.persistence.orm import Base


class _ConnectionGuard:
    """内存库单连接的跨线程互斥（RLock）。

    StaticPool 把唯一一条 DBAPI 连接交给所有会话：并发命令的事务会在同一
    条连接上交错——一条只读命令的提交会把另一个线程写到一半的事务一并
    持久化（失败的命令被部分提交：已办留痕、任务消失、流程卡死）。本守卫
    使「持连接＝持锁」：事务互斥、跨线程排队。RLock 的两个性质正是这里
    需要的：同线程嵌套会话（故障单记录等在持连接时另开会话）可重入；属主
    线程死亡自动释放——归还事件全部被跳过的异常路径不再挂死他人。

    释放绑定 checkin 与 reset 两个事件（幂等）：池的 GC/分离归还路径会
    清掉 fairy 引用并**跳过 checkin 事件**（reset 照常触发），只绑 checkin
    会漏释放。

    注意：acquire 绝不抛错（阻塞等待）——在池的 checkout 事件里抛异常会让
    池作废共享连接，:memory: 下次 checkout 会拿到**全新空库**（schema 与
    数据凭空消失，实测）。钩子长阻塞会让其他线程排队等待，这是内存库的
    诚实代价；多线程/长事务宿主请用文件库或 PostgreSQL。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def acquire(self) -> None:
        self._lock.acquire()

    def release(self) -> None:
        # 重复归还（reset+checkin 双事件）或他线程 GC 归还——RLock 下无害
        with contextlib.suppress(RuntimeError):
            self._lock.release()


def _serialize_memory_engine(engine: Engine) -> None:
    guard = _ConnectionGuard()

    @event.listens_for(engine.pool, "checkout")
    def _acquire(*_args: Any) -> None:
        guard.acquire()

    @event.listens_for(engine.pool, "checkin")
    def _release_on_checkin(*_args: Any) -> None:
        guard.release()

    @event.listens_for(engine.pool, "reset")
    def _release_on_reset(*_args: Any) -> None:
        guard.release()


def create_db_engine(url: str, *, pool_size: int = 5, max_overflow: int = 10) -> Engine:
    """创建数据库引擎（SQLite 默认开启外键与 WAL）。

    内存 SQLite 用 StaticPool：所有连接共享同一个内存库（否则每连接各一份
    空库，跨线程/跨会话不可见——幂等并发等场景的隐性坑）。共享的代价是
    单连接：并发事务会互相交错，故挂连接守卫做事务级串行化（跨线程排队、
    同线程可重入）——多线程重负载请用文件库或 PostgreSQL。
    """
    if url.endswith(":memory:") or "mode=memory" in url:
        engine = create_engine(
            url, future=True, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        _serialize_memory_engine(engine)
    else:
        # 非内存库（PG 等）：pre_ping 防防火墙掐断的陈旧连接在下次借出时炸；
        # 池参数经 EngineConfig.pool_size/max_overflow 透传（REST 同步处理器
        # 跑在线程池里，缺省 5+10 对高并发偏小，宿主可调）
        engine = create_engine(
            url,
            future=True,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
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
