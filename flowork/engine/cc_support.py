"""传阅（CcTask）支撑：生成传阅件。已读接口在 M3（TaskService.mark_cc_read）。

recipients 每项为 **CEL 表达式**（spec §4.2 位点统一）：字面人写 ``'u_a'``，
发起人写保留标识符 ``initiator``，函数如 ``manager_of(initiator)``。
求值错按错误二分包 EngineExecutionError（挂起+故障单，§8）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.participants.resolver import resolve_subjects

if TYPE_CHECKING:
    from flowork.definitions.builtin import CcTaskSpec
    from flowork.engine.context import RuntimeNodeContext

__all__ = ["create_cc_items"]


def create_cc_items(ctx: RuntimeNodeContext, spec: CcTaskSpec) -> None:
    """为接收人生成传阅件（recipients 逐项 CEL 求值，null/空=查无传播）。"""
    from flowork.expr.context import expression_functions_of
    from flowork.persistence.orm import CcItemRow
    from flowork.tasks.facts import attach_node, instance_facts, with_prev_task

    config = ctx.kernel.engine.config if ctx.kernel.engine else None
    recipients = resolve_subjects(
        list(spec.recipients),
        with_prev_task(ctx.session, ctx.instance.id, ctx.variables),
        ctx.instance.start_user,
        attach_node(expression_functions_of(config), ctx.session, ctx.instance.id),
        label=spec.code,
        what="传阅接收人",
        instance=instance_facts(ctx.instance),
        now=ctx.kernel.config.now(),
    )
    from flowork.participants.resolver import assert_participant_limit

    assert_participant_limit(
        len(recipients),
        config.max_participants if config is not None else None,
        where=f"传阅节点 {spec.code} 接收人",
    )
    for recipient in recipients:
        ctx.session.add(
            CcItemRow(
                id=_new_id(),
                instance_id=ctx.instance.id,
                activity_code=spec.code,
                recipient=recipient,
                title=spec.name or spec.code,
                context_payload=dict(ctx.variables),
            )
        )
    if recipients:
        ctx.emit(
            "cc_created",
            {"activity_code": spec.code, "recipients": recipients},
        )


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex
