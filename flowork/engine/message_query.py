"""消息查询与同步关联面（spec §7.2 配套面）：correlate_message / waiting / 检索。

从 message_service.py 拆出（单文件 ≤300 行红线）；流转件（订阅/命中/广播）
在 message_flow.py。correlate 对标 Zeebe correlate 端点——「发布方想知道
消息被谁消费」的同步答案；waiting 为等待实例反查（运维刚需）；
list_messages 为消息检索（收尾批 6，spec §12 messages/search 数据源）。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from flowork.persistence.orm import MessageBufferRow, MessageSubscriptionRow, ProcessInstanceRow
from flowork.runtime.lifecycle import FROZEN_INSTANCE_STATES

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.engine.ops import CommandContext
    from flowork.persistence.orm import MessageSubscriptionRow as _SubRow

__all__ = ["correlate_message", "first_open_subscription", "list_messages", "waiting"]


def first_open_subscription(
    ctx: CommandContext,
    name: str,
    correlation_key: str,
    instance_id: str | None = None,
) -> _SubRow | None:
    """首个可消费的 open 订阅（FCFS；跳过暂停/挂起实例——spec §7.2 暂停
    拦截 + 拍板 1：挂起对齐暂停，消息落缓冲、故障解决后补消费）。

    ``instance_id``（全仓 review 2026-10-13 批 1 P0-2）：实例定向匹配——
    signal 面只消费本实例的订阅，防同名同 key 多实例时消息被 FCFS 先到的
    他人实例抢走（副作用落错单）。None 保持全局 FCFS（publish 路径不变）。"""
    stmt = select(MessageSubscriptionRow).where(
        MessageSubscriptionRow.name == name,
        MessageSubscriptionRow.correlation_key == correlation_key,
        MessageSubscriptionRow.state == "open",
    )
    if instance_id is not None:
        stmt = stmt.where(MessageSubscriptionRow.instance_id == instance_id)
    rows = ctx.session.scalars(stmt.order_by(MessageSubscriptionRow.created_at)).all()
    for row in rows:
        instance = ctx.session.get(ProcessInstanceRow, row.instance_id)
        if instance is None or instance.state in FROZEN_INSTANCE_STATES:
            continue
        return row
    return None


def correlate_message(
    engine: ProcessEngine,
    name: str,
    correlation_key: str = "",
    payload: dict[str, Any] | None = None,
    *,
    message_id: str | None = None,
    instance_id: str | None = None,
) -> dict[str, Any] | None:
    """同步关联入口（spec §7.2 配套面，Zeebe correlate 端点同款）。

    **不缓冲**：首个可消费 open 订阅（跳过暂停实例）→ 关联+推进，回传
    签收凭证 ``{matched, duplicate, instance_id, node_id, message_name,
    business_key, definition_code}``；无人在等 → None（消息不留存，调用方
    自决重试/报错）；重复 message_id → 回放首次结果（duplicate=True，
    幂等重放不二次消费）。匹配顺序与 publish 一致：在途订阅 → 启动匹配。

    ``instance_id``（实例定向）：只匹配本实例的 open 订阅——signal 面
    防串单（副作用只落目标实例）；本实例无 open 订阅时**不做启动匹配**
    （定向语义下启动新实例违背调用意图），直接 None。
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
        # 到达事实先落事件（事件 review A3 三路统一；correlate 无缓冲分支，
        # 此前该入口零 received）——重复 message_id 回放不算新到达，不发。
        from flowork.engine.message_flow import emit_message_event
        from flowork.events.domain import DomainEvent

        emit_message_event(
            engine, ctx, DomainEvent.MESSAGE_RECEIVED, name, correlation_key, None, message_id=mid
        )
        from flowork.engine.message_flow import hit_subscription

        subscription = first_open_subscription(ctx, name, correlation_key, instance_id)
        if subscription is not None and hit_subscription(
            ctx, engine, subscription, body, message_id=mid
        ):
            return _receipt_of(ctx, subscription, duplicate=False)
        from flowork.engine.message_start import match_start_on_message

        if instance_id is None and match_start_on_message(
            engine, ctx, name, correlation_key, body, mid
        ):
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


def list_messages(
    engine: ProcessEngine,
    *,
    name: str | None = None,
    correlation_key: str | None = None,
    kind: str | None = None,
    state: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """消息检索（收尾批 6，spec §12 messages/search 数据源）。

    两源合并按时间倒序：buffer（未消费缓冲，TTL 内）∪ subscription（订阅行
    ——open=在等 / matched=已消费定格）。correlation_key 前缀匹配；kind 收窄
    单源；state 仅对 subscription 源生效。分页为内存归并切片（两侧各取
    offset+limit+1 行上界，调用方照 list_jobs 惯例传 limit+1 探测 has_more）。
    """
    take = offset + limit + 1
    rows: list[dict[str, Any]] = []
    with engine.session_factory() as session:
        if kind in (None, "buffer"):
            buf_stmt = select(MessageBufferRow).order_by(MessageBufferRow.received_at.desc())
            if name is not None:
                buf_stmt = buf_stmt.where(MessageBufferRow.name == name)
            if correlation_key:
                buf_stmt = buf_stmt.where(
                    MessageBufferRow.correlation_key.like(f"{correlation_key}%")
                )
            rows.extend(
                {
                    "kind": "buffer",
                    "id": row.id,
                    "name": row.name,
                    "correlation_key": row.correlation_key,
                    "message_id": row.message_id,
                    "payload": row.payload,
                    "occurred_at": row.received_at.isoformat() if row.received_at else None,
                }
                for row in session.scalars(buf_stmt.limit(take))
            )
        if kind in (None, "subscription"):
            sub_stmt = select(MessageSubscriptionRow).order_by(
                MessageSubscriptionRow.created_at.desc()
            )
            if name is not None:
                sub_stmt = sub_stmt.where(MessageSubscriptionRow.name == name)
            if correlation_key:
                sub_stmt = sub_stmt.where(
                    MessageSubscriptionRow.correlation_key.like(f"{correlation_key}%")
                )
            if state is not None:
                sub_stmt = sub_stmt.where(MessageSubscriptionRow.state == state)
            rows.extend(
                {
                    "kind": "subscription",
                    "id": row.id,
                    "instance_id": row.instance_id,
                    "node_id": row.node_id,
                    "name": row.name,
                    "correlation_key": row.correlation_key,
                    "message_id": row.message_id,
                    "state": row.state,
                    "occurred_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in session.scalars(sub_stmt.limit(take))
            )
    rows.sort(key=lambda item: item["occurred_at"] or "", reverse=True)
    return rows


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


# 裸投影形态 payload.<key>：载荷缺该键 → None（缺失≠求值错，对齐 _mapping_values）；复合式严格求值。
_BARE_PAYLOAD_FIELD = __import__("re").compile(r"^payload\.([A-Za-z_][A-Za-z0-9_]*)$")


def resolve_payload_write(
    config: Any, payload: dict[str, Any], mappings: dict[str, str] | None, node_id: str
) -> dict[str, Any]:
    """载荷写入集（自 message_service 下沉守红线）：声明了 output_mappings 按
    映射白名单求值，否则全量合并；裸投影缺键 None（spec §7.2）。"""
    if not mappings:
        return dict(payload)
    from flowork.engine.kernel import EngineExecutionError
    from flowork.expr.cel import FormulaError, evaluate_formula_value
    from flowork.expr.context import expression_context, expression_functions_of

    context = expression_context({"payload": dict(payload)}, now=config.now())
    functions = expression_functions_of(config)
    write: dict[str, Any] = {}
    for target, expression in mappings.items():
        bare = _BARE_PAYLOAD_FIELD.match(expression.strip())
        if bare is not None and bare.group(1) not in (payload or {}):
            write[target] = None  # 裸投影缺键：数据缺失≠求值错（spec §7.2）
            continue
        try:
            write[target] = evaluate_formula_value(expression, context, functions=functions)
        except FormulaError as exc:
            raise EngineExecutionError(
                f"wait {node_id} output_mappings.{target} 求值失败（{expression!r}）：{exc}"
            ) from exc
    return write
