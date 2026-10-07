"""消息服务（kernel-v2 spec §7/P1-1）：缓冲、TTL、按关联键匹配订阅。

- ``publish_message(name, correlation_key, payload, message_id, ttl_seconds)``：
  先到（无 open 订阅）进缓冲区；命中 open 订阅即关联（先来先服务）。
  message_id 全局唯一防重——重复发布静默忽略（幂等）。
- ``subscribe(instance, token, node, name, correlation_key)``：等待方登记订阅；
  登记时反查缓冲区，命中未过期消息立即关联（消息先到竞态）。
- 关联动作（同事务）：订阅置 matched、缓冲行消费、载荷合并进实例变量
  （走 input 契约 node_output 边界，与人工任务完成的写入路径同一条）、
  发 ``message_correlated``。等待节点行为（首版后置，spec §2.4）观察 matched
  后推进；当前引擎侧由 ``signal`` 唤醒广播同款机制衔接。
- ``signal(name)``：广播——唤醒（matched）所有在等该名字的订阅，不带数据。
- correlation_key 为**求值后**键值；CEL 表达式求值属等待节点行为（expr/cel.py）。
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from flowork.clock import utc_now
from flowork.engine.ops import CommandContext
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import MessageBufferRow, MessageSubscriptionRow

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["MessageService"]

DEFAULT_TTL_SECONDS = 86_400


class MessageService:
    """消息/信号的发布-订阅-关联（spec §7）。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine

    def publish_message(
        self,
        name: str,
        correlation_key: str = "",
        payload: dict[str, Any] | None = None,
        *,
        message_id: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> bool:
        """发布消息：命中 open 订阅立即关联（返回 True）；否则入缓冲（False）。

        message_id 重复发布静默忽略（幂等防重，返回 False 且不入库）。
        """

        def _command(ctx: CommandContext) -> bool:
            message_id_ = message_id or uuid.uuid4().hex
            self._purge_expired(ctx)
            # 防重只按 message_id（spec §7）：未消费缓冲 ∪ 已消费订阅的定格记录。
            # 与 (name, correlation_key) 无关——同一业务键的后续事件是合法输入。
            duplicate = (
                ctx.session.scalars(
                    select(MessageBufferRow.id).where(MessageBufferRow.message_id == message_id_)
                ).first()
                is not None
                or ctx.session.scalars(
                    select(MessageSubscriptionRow.id).where(
                        MessageSubscriptionRow.message_id == message_id_
                    )
                ).first()
                is not None
            )
            if duplicate:
                return False

            subscription = ctx.session.scalars(
                select(MessageSubscriptionRow)
                .where(
                    MessageSubscriptionRow.name == name,
                    MessageSubscriptionRow.correlation_key == correlation_key,
                    MessageSubscriptionRow.state == "open",
                )
                .order_by(MessageSubscriptionRow.created_at)
                .limit(1)
            ).first()
            if subscription is not None:
                self._correlate(ctx, subscription, dict(payload or {}), message_id=message_id_)
                return True

            ctx.session.add(
                MessageBufferRow(
                    id=uuid.uuid4().hex,
                    name=name,
                    correlation_key=correlation_key,
                    message_id=message_id_,
                    payload=dict(payload or {}),
                    expires_at=utc_now() + timedelta(seconds=ttl_seconds),
                )
            )
            self._emit(ctx, DomainEvent.MESSAGE_RECEIVED, name, correlation_key, None)
            return False

        return self._engine.execute(_command)

    def subscribe(
        self,
        instance_id: str,
        token_id: str,
        node_id: str,
        name: str,
        correlation_key: str = "",
    ) -> bool:
        """登记订阅；命中缓冲区未过期消息立即关联（消息先到竞态，spec §7）。"""

        def _command(ctx: CommandContext) -> bool:
            ctx.session.add(
                MessageSubscriptionRow(
                    id=uuid.uuid4().hex,
                    instance_id=instance_id,
                    token_id=token_id,
                    node_id=node_id,
                    name=name,
                    correlation_key=correlation_key,
                )
            )
            self._purge_expired(ctx)
            buffered = ctx.session.scalars(
                select(MessageBufferRow)
                .where(
                    MessageBufferRow.name == name,
                    MessageBufferRow.correlation_key == correlation_key,
                )
                .order_by(MessageBufferRow.received_at)
                .limit(1)
            ).first()
            if buffered is None:
                return False
            subscription = ctx.session.scalars(
                select(MessageSubscriptionRow)
                .where(
                    MessageSubscriptionRow.instance_id == instance_id,
                    MessageSubscriptionRow.token_id == token_id,
                    MessageSubscriptionRow.node_id == node_id,
                    MessageSubscriptionRow.state == "open",
                )
                .limit(1)
            ).first()
            assert subscription is not None
            payload = dict(buffered.payload or {})
            message_id = buffered.message_id
            ctx.session.delete(buffered)
            self._correlate(ctx, subscription, payload, message_id=message_id)
            return True

        return self._engine.execute(_command)

    def close_subscriptions(self, instance_id: str, *, node_id: str | None = None) -> int:
        """节点离开/实例终止时关闭订阅（spec §7 生命周期）。"""

        def _command(ctx: CommandContext) -> int:
            stmt = delete(MessageSubscriptionRow).where(
                MessageSubscriptionRow.instance_id == instance_id,
                MessageSubscriptionRow.state == "open",
            )
            if node_id is not None:
                stmt = stmt.where(MessageSubscriptionRow.node_id == node_id)
            result = ctx.session.execute(stmt)
            return int(getattr(result, "rowcount", 0) or 0)

        return self._engine.execute(_command)

    def signal(self, name: str) -> int:
        """广播唤醒（spec §7）：matched 所有在等该名字的 open 订阅，不带数据。"""

        def _command(ctx: CommandContext) -> int:
            self._purge_expired(ctx)
            rows = ctx.session.scalars(
                select(MessageSubscriptionRow).where(
                    MessageSubscriptionRow.name == name,
                    MessageSubscriptionRow.state == "open",
                )
            ).all()
            kernel = self._engine.kernel_for(ctx)
            for row in rows:
                row.state = "matched"
                kernel.record_event(
                    DomainEvent.MESSAGE_CORRELATED,
                    self._correlated_payload(row, {}, None),
                    row.instance_id,
                )
            return len(rows)

        return self._engine.execute(_command)

    # —— 内部 ——

    def _correlate(
        self,
        ctx: CommandContext,
        subscription: MessageSubscriptionRow,
        payload: dict[str, Any],
        *,
        message_id: str | None = None,
    ) -> None:
        """关联成功（同事务）：matched＋载荷合并（契约边界）＋事件。"""
        from flowork.definitions.contract import validate_input_contract
        from flowork.persistence.orm import ProcessInstanceRow

        instance = ctx.session.get(ProcessInstanceRow, subscription.instance_id)
        if instance is not None:
            merged = dict(instance.variables)
            merged.update(payload)
            schema = (json.loads(instance.definition_snapshot) or {}).get("input")
            validate_input_contract(schema, merged, boundary="node_output")
            instance.variables.update(payload)
            ctx.touch(instance)
        subscription.state = "matched"
        subscription.message_id = message_id  # 防重定格（只按 message_id 判）
        kernel = self._engine.kernel_for(ctx)
        kernel.record_event(
            DomainEvent.MESSAGE_CORRELATED,
            self._correlated_payload(subscription, payload, message_id),
            subscription.instance_id,
        )

    def _correlated_payload(
        self,
        subscription: MessageSubscriptionRow,
        payload: dict[str, Any],
        message_id: str | None,
    ) -> dict[str, Any]:
        """message_correlated 统一载荷形状（消息关联与 signal 唤醒同款）。"""
        return {
            "name": subscription.name,
            "correlation_key": subscription.correlation_key,
            "message_id": message_id,
            "node_id": subscription.node_id,
            "payload_keys": sorted(payload),
        }

    def _purge_expired(self, ctx: CommandContext) -> None:
        ctx.session.execute(delete(MessageBufferRow).where(MessageBufferRow.expires_at < utc_now()))

    def _emit(
        self,
        ctx: CommandContext,
        event: DomainEvent,
        name: str,
        correlation_key: str,
        instance_id: str | None,
    ) -> None:
        kernel = self._engine.kernel_for(ctx)
        kernel.record_event(
            event,
            {"name": name, "correlation_key": correlation_key},
            instance_id,
        )
