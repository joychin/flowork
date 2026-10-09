"""start_on_message 启动匹配（spec §7.1）：publish 的第三优先级分支。

在途节点订阅优先（``MessageService.publish`` 既有 FCFS）→ 本模块启动匹配 →
缓冲。启动订阅＝定义派生：published 定义即生效、retire 即失效（查询即匹配，
不落订阅表）；命中即于同一 CommandContext 复用 ``RuntimeService._start``
管线发起实例，并把消息定格为 matched 订阅行（node_id=``(start)``）进
message_id 防重并集——同 message 至多启动一个实例。

部署时刻起算：不回溯消费既有缓冲（7.1，防「一部署冒出一批实例」）。
声明位表达式求值错 / 非 string＝技术故障：抛 FloworkError 使 publish 整体
失败（消息不落库不缓冲），错误信息带定义 code 与表达式。
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from flowork.engine.ops import CommandContext
from flowork.errors import FloworkError
from flowork.events.domain import DomainEvent
from flowork.expr.cel import FormulaError, evaluate_formula_value
from flowork.expr.context import expression_context, expression_functions_of
from flowork.persistence.orm import ProcessDefinitionRow
from flowork.persistence.orm_v2 import MessageSubscriptionRow

if TYPE_CHECKING:
    from flowork.definitions.definition import StartMessageDecl
    from flowork.engine.engine import ProcessEngine

__all__ = ["START_NODE_ID", "match_start_on_message"]

START_NODE_ID = "(start)"


def match_start_on_message(
    engine: ProcessEngine,
    ctx: CommandContext,
    name: str,
    correlation_key: str,
    payload: dict[str, Any],
    message_id: str,
) -> bool:
    """启动匹配（publish 同事务）：命中任一定义即发起实例，返回 True。

    多定义声明同名消息时取首个命中（定义序）；input 契约越界等启动失败
    沿 _start 原样抛出（publish 整体失败，技术故障不静默）。
    """
    for code, decl in _declarations(ctx, name):
        context = expression_context({"payload": dict(payload)})
        functions = expression_functions_of(engine.config)
        if decl.correlation_key and (
            _string_of(code, "correlation_key", decl.correlation_key, context, functions)
            != correlation_key
        ):
            continue  # 防串单：载荷求值键 ≠ 发布携带键
        variables = {
            target: _value_of(code, "input_mappings", expression, context, functions)
            for target, expression in decl.input_mappings.items()
        }
        start_user = (
            _string_of(code, "start_user", decl.start_user, context, functions)
            if decl.start_user
            else f"message:{decl.name}"
        )
        business_key = (
            _string_of(code, "business_key", decl.business_key, context, functions)
            if decl.business_key
            else None
        )
        instance_id = engine.runtime._start(
            ctx,
            code,
            start_user,
            variables,
            business_key,
            None,
            None,
            None,
            None,
            None,
            "reject",
            start_message={"name": name, "message_id": message_id},
        )
        ctx.session.add(
            MessageSubscriptionRow(
                id=uuid.uuid4().hex,
                instance_id=instance_id,
                token_id="",
                node_id=START_NODE_ID,
                name=name,
                correlation_key=correlation_key,
                state="matched",
                message_id=message_id,  # 防重定格（buffer ∪ 订阅定格并集）
            )
        )
        kernel = engine.kernel_for(ctx)
        kernel.record_event(
            DomainEvent.MESSAGE_CORRELATED,
            {
                "name": name,
                "correlation_key": correlation_key,
                "message_id": message_id,
                "node_id": START_NODE_ID,
                "payload_keys": sorted(payload),
            },
            instance_id,
        )
        return True
    return False


def _declarations(ctx: CommandContext, name: str) -> list[tuple[str, StartMessageDecl]]:
    """name 命中的启动声明（每 code 取最新 published 的顶层声明，定义序）。

    直接读快照顶层字段（不经 NodeRegistry 解析节点）——启动订阅是定义派生
    态，查询即匹配；多 major 维护分支以 (major, revision) 最大者为最新。
    """
    from flowork.definitions.definition import StartMessageDecl

    rows = ctx.session.execute(
        select(
            ProcessDefinitionRow.code,
            ProcessDefinitionRow.major,
            ProcessDefinitionRow.revision,
            ProcessDefinitionRow.content,
        )
        .where(ProcessDefinitionRow.status == "published")
        .order_by(
            ProcessDefinitionRow.code,
            ProcessDefinitionRow.major.desc(),
            ProcessDefinitionRow.revision.desc(),
        )
    ).all()
    latest: dict[str, str] = {}
    for code, _major, _revision, content in rows:
        if code not in latest:
            latest[code] = content
    matched: list[tuple[str, StartMessageDecl]] = []
    for code, content in latest.items():
        raw_list = (json.loads(content) or {}).get("start_on_message") or []
        for raw in raw_list:
            decl = StartMessageDecl.model_validate(raw)
            if decl.name == name:
                matched.append((code, decl))
    return matched


def _value_of(
    code: str, field: str, expression: str, context: dict[str, Any], functions: Any
) -> Any:
    """消息上下文求值（任意值）；错＝技术故障。"""
    try:
        return evaluate_formula_value(expression, context, functions=functions)
    except FormulaError as exc:
        raise FloworkError(
            f"start_on_message.{field} 求值失败（{code}，{expression!r}）：{exc}"
        ) from exc


def _string_of(
    code: str, field: str, expression: str, context: dict[str, Any], functions: Any
) -> str:
    """须 string 的声明位（correlation/start_user/business_key）求值。"""
    value = _value_of(code, field, expression, context, functions)
    if not isinstance(value, str):
        raise FloworkError(
            f"start_on_message.{field} 须求值为 string"
            f"（{code}，{expression!r} → {type(value).__name__}）"
        )
    return value
