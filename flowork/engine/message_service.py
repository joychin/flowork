"""消息服务（kernel-v2 spec §7/P1-1）：缓冲、TTL、按关联键匹配订阅。

- ``publish_message``：先到进缓冲（TTL），命中即关联（FCFS）；message_id 全局
  唯一防重。``subscribe``：登记并反查缓冲（消息先到竞态）；命中即完整关联动作
  （同事务）：matched＋载荷写入＋ ``message_correlated``＋推进（等待节点）。
- ``correlate_message``：同步关联（不缓冲）——签收凭证/无人在等 None/幂等回放；
  ``instance_id`` 定向＝signal 面只消费本实例订阅（review 2026-10-13 防串单）。
- ``waiting``/``list_messages``：反查与检索（运维面）；``signal`` 广播唤醒。
- 暂停实例不消费（落缓冲恢复补消费）；查询件下沉 engine.message_query。
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

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
            # 防重只按 message_id（spec §7）：未消费缓冲 ∪ 已消费定格记录，与关联键无关。
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

            # 到达事实先落事件（事件 review A3 三路统一，语义见方法 docstring）
            from flowork.engine.message_flow import emit_message_event

            emit_message_event(
                self._engine,
                ctx,
                DomainEvent.MESSAGE_RECEIVED,
                name,
                correlation_key,
                None,
                message_id=message_id_,
            )

            # 在途订阅优先（FCFS）；暂停实例跳过（spec §7.2 全动作拦截，消息落
            # 下一优先级），命中走完整管线（映射+推进）。
            from flowork.engine.message_query import first_open_subscription

            subscription = first_open_subscription(ctx, name, correlation_key)
            if subscription is not None:
                from flowork.engine.message_flow import hit_subscription

                if hit_subscription(
                    ctx, self._engine, subscription, dict(payload or {}), message_id=message_id_
                ):
                    return True

            # 启动匹配（spec §7.1，第三优先级）：在途订阅未消费 → 查定义派生的
            # 启动订阅；命中即同事务发起实例并定格 message_id（不回溯缓冲）。
            from flowork.engine.message_start import match_start_on_message

            if match_start_on_message(
                self._engine, ctx, name, correlation_key, dict(payload or {}), message_id_
            ):
                return True

            ctx.session.add(
                MessageBufferRow(
                    id=uuid.uuid4().hex,
                    name=name,
                    correlation_key=correlation_key,
                    message_id=message_id_,
                    payload=dict(payload or {}),
                    # TTL 与其他写路径同源（P1-18）：此前墙钟
                    expires_at=self._engine.config.now() + timedelta(seconds=ttl_seconds),
                )
            )
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
            from flowork.engine.message_flow import subscribe_open

            return subscribe_open(
                ctx,
                self._engine,
                instance_id=instance_id,
                token_id=token_id,
                node_id=node_id,
                name=name,
                correlation_key=correlation_key,
            )

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
        from flowork.engine.message_flow import broadcast_signal

        return broadcast_signal(self._engine, name)

    def correlate_message(
        self,
        name: str,
        correlation_key: str = "",
        payload: dict[str, Any] | None = None,
        *,
        message_id: str | None = None,
        instance_id: str | None = None,
    ) -> dict[str, Any] | None:
        """同步关联（不缓冲）：命中回传签收凭证，无人在等返回 None（§7.2）；
        ``instance_id`` 定向＝只消费本实例订阅（review P0-2 防串单）。"""
        from flowork.engine import message_query as _mq

        return _mq.correlate_message(
            self._engine,
            name,
            correlation_key,
            payload,
            message_id=message_id,
            instance_id=instance_id,
        )

    def waiting(
        self, name: str | None = None, correlation_key: str | None = None
    ) -> list[dict[str, Any]]:
        """等待实例反查（open 订阅，运维面）。"""
        from flowork.engine import message_query as _mq

        return _mq.waiting(self._engine, name=name, correlation_key=correlation_key)

    def list_messages(
        self,
        *,
        name: str | None = None,
        correlation_key: str | None = None,
        kind: str | None = None,
        state: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """消息检索（spec §12 messages/search 数据源；缓冲∪订阅行，见 message_query）。"""
        from flowork.engine import message_query as _mq

        return _mq.list_messages(
            self._engine,
            name=name,
            correlation_key=correlation_key,
            kind=kind,
            state=state,
            offset=offset,
            limit=limit,
        )

    # —— 内部 ——

    def _correlate(
        self,
        ctx: CommandContext,
        subscription: MessageSubscriptionRow,
        payload: dict[str, Any],
        *,
        message_id: str | None = None,
        mappings: dict[str, str] | None = None,
    ) -> None:
        """关联成功（同事务）：matched＋载荷写入（映射或全量，契约边界）＋事件。

        mappings（spec §7）：右侧表达式于消息上下文（payload.*+now）求值，缺键
        None；未声明全量合并兜底。映射求值错＝技术故障（publish 失败不落库）。
        """
        from flowork.definitions.contract import validate_input_contract
        from flowork.engine.message_query import resolve_payload_write
        from flowork.persistence.orm import ProcessInstanceRow

        write = resolve_payload_write(self._engine.config, payload, mappings, subscription.node_id)
        instance = ctx.session.get(ProcessInstanceRow, subscription.instance_id)
        if instance is not None:
            # touch 先于载荷合并：变量留痕 before 快照须在写入前留存
            ctx.touch(instance)
            merged = dict(instance.variables)
            merged.update(write)
            schema = (json.loads(instance.definition_snapshot) or {}).get("input")
            validate_input_contract(schema, merged, boundary="node_output")
            instance.variables.update(write)
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
        from flowork.engine.message_flow import correlated_payload

        return correlated_payload(subscription, payload, message_id)

    def _purge_expired(self, ctx: CommandContext) -> None:
        from flowork.engine.message_flow import purge_expired

        purge_expired(ctx.session, now=self._engine.config.now())
