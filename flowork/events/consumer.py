"""outbox 消费器（kernel-v2 spec §10/P0-3）：至少一次分发＋按 id 幂等。

- ``OutboxConsumer.poll()``：取未送达行（按发生时间有序）→ 包装 CloudEvents
  信封 → 交给 EventSink → 标记 delivered_at（同事务）。先送达后标记：送达后
  崩溃会重发，因此交付语义是「至少一次」。
- ``IdempotentSink``：按信封 id 去重的消费端装饰器（有界记忆集）——重发不重算。
- ``purge_delivered()``：清理已送达且超保留期的行（默认 90 天，spec §14）。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from flowork.clock import utc_now
from flowork.events.envelope import envelope_of
from flowork.persistence.orm import EventOutboxRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

__all__ = ["IdempotentSink", "OutboxConsumer"]

DEFAULT_RETENTION_DAYS = 90


class IdempotentSink:
    """按 CloudEvents id 去重的消费端（验收：消费端按 id 幂等）。

    记忆集有界（默认 10 万条）：超出后最旧 id 淘汰——同 id 重发在窗口内必然去重；
    窗口外的重发由业务侧落库幂等兜底（文档级承诺）。
    """

    def __init__(self, emit: Callable[[dict[str, Any]], None], *, memory: int = 100_000) -> None:
        self._emit = emit
        self._seen: set[str] = set()
        self._order: deque[str] = deque()
        self._memory = memory

    def __call__(self, envelope: dict[str, Any]) -> None:
        event_id = str(envelope.get("id"))
        if event_id in self._seen:
            return  # 至少一次交付下的重发，幂等丢弃
        self._seen.add(event_id)
        self._order.append(event_id)
        if len(self._order) > self._memory:
            self._seen.discard(self._order.popleft())
        self._emit(envelope)


class OutboxConsumer:
    """轮询 wf_event_outbox 并向 EventSink 分发 CloudEvents 信封。"""

    def __init__(
        self,
        factory: sessionmaker[Session],
        sink: Callable[[dict[str, Any]], None],
        *,
        engine_id: str = "default",
        batch_size: int = 100,
    ) -> None:
        """sink＝信封回调（``Callable[[envelope], None]``；IdempotentSink 即满足）。"""
        self._factory = factory
        self._sink = sink
        self._engine_id = engine_id
        self._batch_size = batch_size

    def poll(self) -> int:
        """分发一批未送达事件，返回本批条数（0＝无待分发）。"""
        now = utc_now()
        with self._factory() as session, session.begin():
            rows = session.scalars(
                select(EventOutboxRow)
                .where(EventOutboxRow.delivered_at.is_(None))
                .order_by(EventOutboxRow.occurred_at, EventOutboxRow.id)
                .limit(self._batch_size)
            ).all()
            for row in rows:
                self._sink(envelope_of(row, self._engine_id))
                row.delivered_at = now
        return len(rows)

    def drain(self, *, max_batches: int = 100) -> int:
        """循环 poll 直到清空（返回总条数）；max_batches 防失控。"""
        total = 0
        for _ in range(max_batches):
            delivered = self.poll()
            total += delivered
            if delivered < self._batch_size:
                break
        return total

    def purge_delivered(self, *, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
        """删除已送达且超过保留期的行（spec §14：消费确认后可清理，默认 90 天）。"""
        cutoff: datetime = utc_now() - timedelta(days=retention_days)
        with self._factory() as session, session.begin():
            stale_ids = session.scalars(
                select(EventOutboxRow.id).where(
                    EventOutboxRow.delivered_at.is_not(None),
                    EventOutboxRow.delivered_at < cutoff,
                )
            ).all()
            if stale_ids:
                session.execute(delete(EventOutboxRow).where(EventOutboxRow.id.in_(stale_ids)))
        return len(stale_ids)
