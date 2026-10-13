"""消息服务（kernel-v2 spec §7/P1-1）：缓冲、TTL、按关联键匹配订阅。

- ``publish_message``：先到（无 open 订阅）进缓冲（TTL）；命中即关联（FCFS）；
  message_id 全局唯一防重（重复静默忽略）。
- ``subscribe``：登记订阅并反查缓冲（消息先到竞态）；命中即完整关联动作
  （同事务）：matched＋载荷写入（按等待节点 output 映射，缺省全量；input
  契约 node_output 边界把关）＋ ``message_correlated``＋推进（等待节点）。
- ``correlate_message``：同步关联（不缓冲）——命中回传签收凭证/无人在等
  None/重复幂等回放（spec §7.2 配套面，Zeebe correlate 同款）。
- ``waiting``/``list_messages``：订阅反查与消息检索（运维面）；``signal`` 广播唤醒。
- 暂停实例不消费（消息落缓冲，恢复补消费）；查询件下沉 engine.message_query（守红线）。
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

# 裸投影形态 payload.<key>：载荷缺该键 → None（数据缺失≠求值错，对齐
# _mapping_values 的 _BARE_IDENT 缺键 None 先例）；复合表达式严格求值。
_BARE_PAYLOAD_FIELD = __import__("re").compile(r"^payload\.([A-Za-z_][A-Za-z0-9_]*)$")


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

            # 在途订阅优先（FCFS）；暂停实例的订阅跳过（全动作拦截——spec §7.2：
            # 暂停不消费，消息落下一优先级），命中走完整管线（映射+推进）。
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
                    # TTL 与其他写路径同源（code-review 2026-10 P1-18）：此前墙钟
                    expires_at=self._engine.config.now() + timedelta(seconds=ttl_seconds),
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

    def _resolve_write(
        self, payload: dict[str, Any], mappings: dict[str, str] | None, node_id: str
    ) -> dict[str, Any]:
        """载荷写入集：声明了 output_mappings 按映射白名单，否则全量合并。"""
        if not mappings:
            return dict(payload)
        from flowork.engine.kernel import EngineExecutionError
        from flowork.expr.cel import FormulaError, evaluate_formula_value
        from flowork.expr.context import expression_context, expression_functions_of

        context = expression_context({"payload": dict(payload)}, now=self._engine.config.now())
        functions = expression_functions_of(self._engine.config)
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

    def correlate_message(
        self,
        name: str,
        correlation_key: str = "",
        payload: dict[str, Any] | None = None,
        *,
        message_id: str | None = None,
    ) -> dict[str, Any] | None:
        """同步关联（不缓冲）：命中回传签收凭证，无人在等返回 None（§7.2）。"""
        from flowork.engine import message_query as _mq

        return _mq.correlate_message(
            self._engine, name, correlation_key, payload, message_id=message_id
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

        mappings（spec §7 原文「按节点 output 映射写入」）：右侧表达式于消息
        上下文（payload.* + now）求值，缺键得 None；未声明 → 全量合并兜底
        （Zeebe 默认同款）。映射求值错＝技术故障（publish 失败，消息不落库）。
        """
        from flowork.definitions.contract import validate_input_contract
        from flowork.persistence.orm import ProcessInstanceRow

        write = self._resolve_write(payload, mappings, subscription.node_id)
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

    def _emit(
        self,
        ctx: CommandContext,
        event: DomainEvent,
        name: str,
        correlation_key: str,
        instance_id: str | None,
    ) -> None:
        from flowork.engine.message_flow import emit_message_event

        emit_message_event(self._engine, ctx, event, name, correlation_key, instance_id)
