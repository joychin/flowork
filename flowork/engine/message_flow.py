"""消息流转 session 级共用件（spec §7/§7.2）：订阅登记/命中推进/广播/恢复反查。

从 message_service.py 拆出（单文件 ≤300 行红线）。三个入口都以 CommandContext
+ engine 为参（命令内复用，不经 engine.execute 二次开命令）：

- ``subscribe_open``：登记订阅并反查缓冲（消息先到竞态，FCFS）——publish 的
  公开 subscribe 与 WaitBehavior.on_enter 同管线；
- ``hit_subscription``：命中 open 订阅的完整动作——按节点 output_mappings
  关联（缺省全量合并）→ 推进等待令牌（finish_wait，同事务）；暂停拦截把门；
- ``retro_poll_instance``：实例恢复后 open 订阅补消费（暂停期间消息落缓冲的
  语义闭环，spec §7.2 暂停=全动作拦截）；
- ``broadcast_signal``：signal 公开面的命令体。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from flowork.persistence.orm import MessageBufferRow, MessageSubscriptionRow, ProcessInstanceRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.engine.engine import ProcessEngine
    from flowork.engine.ops import CommandContext
    from flowork.persistence.orm import MessageSubscriptionRow as _SubRow

__all__ = [
    "broadcast_signal",
    "correlated_payload",
    "emit_message_event",
    "hit_subscription",
    "purge_expired",
    "retro_poll_instance",
    "subscribe_open",
]


def subscribe_open(
    ctx: CommandContext,
    engine: ProcessEngine,
    *,
    instance_id: str,
    token_id: str,
    node_id: str,
    name: str,
    correlation_key: str = "",
) -> bool:
    """登记订阅；命中缓冲区未过期消息立即走完整命中管线（返回 True）。"""
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
    service = engine.messages
    service._purge_expired(ctx)
    buffered = _oldest_buffered(ctx.session, name, correlation_key)
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
    consumed = hit_subscription(
        ctx, engine, subscription, dict(buffered.payload or {}), message_id=buffered.message_id
    )
    if consumed:
        ctx.session.delete(buffered)
        return True
    return False  # 暂停拦截等场景：消息留缓冲，订阅保持


def hit_subscription(
    ctx: CommandContext,
    engine: ProcessEngine,
    subscription: _SubRow,
    payload: dict[str, Any],
    *,
    message_id: str | None,
) -> bool:
    """命中 open 订阅的完整动作：关联（含映射）→ 推进等待令牌（同事务）。

    暂停拦截（既有全动作拦截原则）：实例 paused 不消费，返回 False——
    publish 侧消息继续走下一优先级；令牌已被取消/完结（竞速败者）同样
    返回 False，命中事实不重复作用。
    """
    from flowork.persistence.orm import TokenRow

    instance = ctx.session.get(ProcessInstanceRow, subscription.instance_id)
    if instance is None or instance.state == "paused":
        return False
    kernel = engine.kernel_for(ctx)
    node = kernel.definition_of(ctx.session, instance).node(subscription.node_id)
    engine.messages._correlate(
        ctx, subscription, payload, message_id=message_id, mappings=_wait_mappings_of(node)
    )
    token = ctx.session.get(TokenRow, subscription.token_id)
    behavior_cls = kernel.registry.behavior_cls(node.kind) if node is not None else None
    if (
        behavior_cls is None
        or not getattr(behavior_cls, "advances_on_message", False)
        or token is None
        or token.state != "unfinished"
    ):
        return True  # 非等待节点/令牌已完结：仅关联定格（宿主纯订阅用法），不推进

    from flowork.tasks.wait import finish_wait

    finish_wait(
        kernel,
        ctx.session,
        instance,
        token,
        result="matched",
        message_name=subscription.name,
        message_id=message_id,
    )
    return True


def retro_poll_instance(
    ctx: CommandContext, engine: ProcessEngine, instance: ProcessInstanceRow
) -> int:
    """恢复后补消费：实例的 open 订阅逐条反查缓冲（FCFS），命中即关联+推进。"""
    subscriptions = ctx.session.scalars(
        select(MessageSubscriptionRow).where(
            MessageSubscriptionRow.instance_id == instance.id,
            MessageSubscriptionRow.state == "open",
        )
    ).all()
    consumed = 0
    for subscription in subscriptions:
        buffered = _oldest_buffered(ctx.session, subscription.name, subscription.correlation_key)
        if buffered is None:
            continue
        if hit_subscription(
            ctx, engine, subscription, dict(buffered.payload or {}), message_id=buffered.message_id
        ):
            ctx.session.delete(buffered)
            consumed += 1
    return consumed


def _oldest_buffered(session: Session, name: str, correlation_key: str) -> MessageBufferRow | None:
    return session.scalars(
        select(MessageBufferRow)
        .where(
            MessageBufferRow.name == name,
            MessageBufferRow.correlation_key == correlation_key,
        )
        .order_by(MessageBufferRow.received_at)
        .limit(1)
    ).first()


def _wait_mappings_of(node: Any) -> dict[str, str] | None:
    """等待节点的 output_mappings（缺省 None=全量合并，Zeebe 默认同款）。"""
    from flowork.definitions.wait_node import WaitSpec

    if isinstance(node, WaitSpec) and node.output_mappings:
        return dict(node.output_mappings)
    return None


def correlated_payload(
    subscription: _SubRow, payload: dict[str, Any], message_id: str | None
) -> dict[str, Any]:
    """message_correlated 统一载荷形状（消息关联与 signal 唤醒同款）。"""
    return {
        "name": subscription.name,
        "correlation_key": subscription.correlation_key,
        "message_id": message_id,
        "node_id": subscription.node_id,
        "payload_keys": sorted(payload),
    }


def purge_expired(session: Session, *, now: Any = None) -> None:
    """清理过期缓冲行（TTL；now＝引擎统一时钟，缺省墙钟仅留无引擎调用兜底）。"""

    from sqlalchemy import delete

    from flowork.clock import utc_now

    session.execute(
        delete(MessageBufferRow).where(MessageBufferRow.expires_at < (now or utc_now()))
    )


def emit_message_event(
    engine: ProcessEngine,
    ctx: CommandContext,
    event: Any,
    name: str,
    correlation_key: str,
    instance_id: str | None,
) -> None:
    """消息面事件发射（MESSAGE_RECEIVED 等的薄包装）。"""
    kernel = engine.kernel_for(ctx)
    kernel.record_event(event, {"name": name, "correlation_key": correlation_key}, instance_id)


def broadcast_signal(engine: ProcessEngine, name: str) -> int:
    """广播唤醒：matched 所有在等该名字的 open 订阅，不带数据（spec §7）。

    每条订阅走 hit_subscription 完整管线（内核 code-review 2026-10 P0-1）：
    等待节点经 finish_wait 推进（spec §7.2 三路收口的第三路，signal 唤醒
    wait.message_id 恒 null）；宿主纯订阅仅关联定格；暂停实例的订阅跳过
    （全动作拦截，保持 open 由恢复反查补消费）。
    """

    def _command(ctx: CommandContext) -> int:
        purge_expired(ctx.session, now=engine.config.now())
        rows = ctx.session.scalars(
            select(MessageSubscriptionRow).where(
                MessageSubscriptionRow.name == name,
                MessageSubscriptionRow.state == "open",
            )
        ).all()
        matched = 0
        for row in rows:
            if hit_subscription(ctx, engine, row, {}, message_id=None):
                matched += 1
        return matched

    return engine.execute(_command)
