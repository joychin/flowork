"""Command 管线：所有引擎操作经拦截器链执行（借鉴 Activiti/Camunda）。

拦截器顺序（外→内）：Metrics（计时）→ Transaction（提交/回滚）→
LockRetry（乐观锁冲突重试）→ HistoryFlush（事件缓冲落 outbox）→ 业务命令。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from flowork.errors import FloworkError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import EventOutboxRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.engine.engine import ProcessEngine

__all__ = ["CommandContext", "LockConflictError", "execute_command"]

logger = logging.getLogger("flowork")

T = TypeVar("T")


class LockConflictError(FloworkError):
    """乐观锁冲突且重试耗尽。"""


@dataclass
class CommandContext:
    """一次命令的执行环境：会话 + 事件缓冲。"""

    engine: ProcessEngine
    session: Session
    events: list[tuple[str, dict[str, Any], str | None]] = field(default_factory=list)


def _flush_history(ctx: CommandContext) -> None:
    """HistoryFlush 拦截器：把命令期间缓冲的领域事件写入 outbox（同事务）。"""
    import uuid

    from flowork.engine.config import should_record_event

    for name, payload, instance_id in ctx.events:
        if not should_record_event(ctx.engine.config, name):
            continue
        ctx.session.add(
            EventOutboxRow(
                id=uuid.uuid4().hex,
                event_name=name,
                instance_id=instance_id,
                payload=payload,
            )
        )
    ctx.session.flush()


def _run_once(engine: ProcessEngine, command: Callable[[CommandContext], T]) -> T:
    session = engine.session_factory()
    ctx = CommandContext(engine=engine, session=session, events=[])
    try:
        # Transaction 拦截器：单命令单事务
        with session.begin():
            result = command(ctx)
            _flush_history(ctx)
        return result
    finally:
        session.close()


def execute_command(engine: ProcessEngine, command: Callable[[CommandContext], T]) -> T:
    """执行一条引擎命令（Metrics → LockRetry → Transaction → HistoryFlush）。"""
    started = time.perf_counter()  # Metrics 拦截器（默认仅记录调试日志）
    attempts = max(1, engine.config.lock_retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = _run_once(engine, command)
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.debug("command ok attempt=%d elapsed_ms=%.1f", attempt, elapsed_ms)
            return result
        except Exception as exc:  # LockRetry 拦截器：仅对乐观锁冲突重试
            if not _is_lock_conflict(exc):
                raise
            last_error = exc
            logger.warning("乐观锁冲突，第 %d/%d 次重试", attempt, attempts)
    raise LockConflictError(f"乐观锁冲突重试 {attempts} 次后仍失败") from last_error


def _is_lock_conflict(exc: Exception) -> bool:
    """SQLAlchemy 乐观锁（version_id）冲突的判定。"""
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm.exc import StaleDataError

    if isinstance(exc, StaleDataError):
        return True
    if isinstance(exc, SQLAlchemyError):
        return "database is locked" in str(exc).lower()
    return False


def emit(
    kernel_events: list[tuple[str, dict[str, Any], str | None]],
    event: DomainEvent | str,
    payload: dict[str, Any],
    instance_id: str | None,
) -> None:
    """便捷：向事件缓冲追加一条（内核内部使用）。"""
    name = event.value if isinstance(event, DomainEvent) else str(event)
    kernel_events.append((name, payload, instance_id))
