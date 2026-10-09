"""消息查询与同步关联面（spec §7.2 配套面）：correlate_message / waiting。

从 message_service.py 拆出（单文件 ≤300 行红线）；流转件（订阅/命中/广播）
在 message_flow.py。correlate 对标 Zeebe correlate 端点——「发布方想知道
消息被谁消费」的同步答案；waiting 为等待实例反查（运维刚需）。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from flowork.persistence.orm import MessageSubscriptionRow, ProcessInstanceRow

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.engine.ops import CommandContext
    from flowork.persistence.orm import MessageSubscriptionRow as _SubRow

__all__ = ["correlate_message", "waiting"]


def correlate_message(
    engine: ProcessEngine,
    name: str,
    correlation_key: str = "",
    payload: dict[str, Any] | None = None,
    *,
    message_id: str | None = None,
) -> dict[str, Any] | None:
    """同步关联入口（spec §7.2 配套面，Zeebe correlate 端点同款）。

    **不缓冲**：首个可消费 open 订阅（跳过暂停实例）→ 关联+推进，回传
    签收凭证 ``{matched, duplicate, instance_id, node_id, message_name,
    business_key, definition_code}``；无人在等 → None（消息不留存，调用方
    自决重试/报错）；重复 message_id → 回放首次结果（duplicate=True，
    幂等重放不二次消费）。匹配顺序与 publish 一致：在途订阅 → 启动匹配。
    """

    def _command(ctx: CommandContext) -> dict[str, Any] | None:
        engine.messages._purge_expired(ctx)
        mid = message_id or uuid.uuid4().hex
        body = dict(payload or {})
        stamped = ctx.session.scalars(
            select(MessageSubscriptionRow).where(MessageSubscriptionRow.message_id == mid)
        ).first()
        if stamped is not None:
            return _receipt_of(ctx, stamped, duplicate=True)  # 已消费：幂等回放
        from flowork.engine.message_flow import hit_subscription

        subscription = engine.messages._first_open_subscription(ctx, name, correlation_key)
        if subscription is not None and hit_subscription(
            ctx, engine, subscription, body, message_id=mid
        ):
            return _receipt_of(ctx, subscription, duplicate=False)
        from flowork.engine.message_start import match_start_on_message

        if match_start_on_message(engine, ctx, name, correlation_key, body, mid):
            started = ctx.session.scalars(
                select(MessageSubscriptionRow).where(MessageSubscriptionRow.message_id == mid)
            ).first()
            if started is not None:  # 启动定格行（node_id=(start)）
                return _receipt_of(ctx, started, duplicate=False)
        return None

    return engine.execute(_command)


def waiting(
    engine: ProcessEngine,
    *,
    name: str | None = None,
    correlation_key: str | None = None,
) -> list[dict[str, Any]]:
    """等待实例反查（open 订阅）：谁在等什么、等了多久——运维刚需面。"""
    with engine.session_factory() as session:
        stmt = (
            select(MessageSubscriptionRow, ProcessInstanceRow)
            .where(
                MessageSubscriptionRow.state == "open",
                MessageSubscriptionRow.instance_id == ProcessInstanceRow.id,
            )
            .order_by(MessageSubscriptionRow.created_at)
        )
        if name is not None:
            stmt = stmt.where(MessageSubscriptionRow.name == name)
        if correlation_key is not None:
            stmt = stmt.where(MessageSubscriptionRow.correlation_key == correlation_key)
        return [
            {
                "instance_id": row.instance_id,
                "node_id": row.node_id,
                "name": row.name,
                "correlation_key": row.correlation_key,
                "definition_code": instance.definition_code,
                "business_key": instance.business_key,
                "waited_since": row.created_at.isoformat() if row.created_at else None,
            }
            for row, instance in session.execute(stmt).all()
        ]


def _receipt_of(ctx: CommandContext, subscription: _SubRow, *, duplicate: bool) -> dict[str, Any]:
    """签收凭证：被哪个实例的哪个节点消费（对账用业务键一并回传）。"""
    instance = ctx.session.get(ProcessInstanceRow, subscription.instance_id)
    return {
        "matched": True,
        "duplicate": duplicate,
        "instance_id": subscription.instance_id,
        "node_id": subscription.node_id,
        "message_name": subscription.name,
        "business_key": instance.business_key if instance is not None else None,
        "definition_code": instance.definition_code if instance is not None else None,
    }
