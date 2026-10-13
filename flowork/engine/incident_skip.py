"""故障修复动作 skip 的手术实现（自 incident_service.py 拆出守 300 行红线）。

跳过故障节点：未完令牌视为完成，按条件路由激活后继（管理员手术，绕放行门）。
事件面：node_cancelled/task_cancelled 由 cancel_token 咽喉统一发（by=incident:skip，
A4）；收尾置回 running 时补 process_resumed（挂起/恢复对称，复核批）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.events.domain import DomainEvent
from flowork.runtime.lifecycle import InstanceState, TokenState

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.engine.kernel import Kernel
    from flowork.engine.ops import CommandContext
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["skip_failed_nodes"]


def skip_failed_nodes(
    engine: ProcessEngine, kernel: Kernel, ctx: CommandContext, instance_id: str, actor: str
) -> None:
    """跳过故障节点（incident resolve 动作之一；同事务内联执行）。"""
    from flowork.engine.route_audit import RoutingAudit, persist_route_decision
    from flowork.engine.routing import evaluation_variables, select_outgoing
    from flowork.expr.context import expression_functions_of
    from flowork.persistence.orm import ProcessInstanceRow
    from flowork.tasks.facts import attach_node

    instance: ProcessInstanceRow | None = ctx.session.get(ProcessInstanceRow, instance_id)
    assert instance is not None
    definition = kernel.definition_of(ctx.session, instance)
    base_functions = expression_functions_of(engine.config)
    for token in kernel.tokens(ctx.session, instance_id, states=(TokenState.UNFINISHED.value,)):
        node = definition.node(token.activity_code)
        # node_cancelled/task_cancelled 由 cancel_token 咽喉统一发（A4，
        # by=incident:skip）——此处不再手发防双发
        kernel.cancel_token(ctx.session, token, cause="incident:skip", actor=actor)
        if node is None:
            continue
        audit = RoutingAudit()
        outgoing = select_outgoing(
            definition.outgoing(node.code),
            evaluation_variables(
                instance, instance.variables, session=ctx.session, now=engine.config.now()
            ),
            mode="all_match",
            functions=attach_node(
                base_functions, ctx.session, instance.id, node_calls=audit.node_calls
            ),
            audit=audit,
        )
        persist_route_decision(ctx.session, instance_id, token.id, audit)
        for flow in outgoing:
            target: Any = definition.node(flow.target)
            if target is not None:
                kernel.activate(ctx.session, instance, target, source_activity_code=node.code)
    if instance.state == InstanceState.SUSPENDED.value:
        instance.state = InstanceState.RUNNING.value
        kernel.record_event(
            DomainEvent.PROCESS_RESUMED,
            {"actor": actor, "reason": "incident:skip", "deferred_minutes": None},
            instance_id,
        )
    ctx.touch(instance)
