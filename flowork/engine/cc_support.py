"""传阅（CcTask）支撑：生成传阅件。已读接口在 M3（TaskService.mark_cc_read）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.events.domain import DomainEvent

if TYPE_CHECKING:
    from flowork.definitions.builtin import CcTaskSpec
    from flowork.engine.context import RuntimeNodeContext

__all__ = ["cc_recipients_of", "create_cc_items"]


def create_cc_items(ctx: RuntimeNodeContext, spec: CcTaskSpec) -> None:
    """为接收人生成传阅件（originator 解析为发起人）。"""
    from flowork.persistence.orm import CcItemRow

    originator = ctx.instance.start_user
    recipients = [originator if r == "originator" else r for r in spec.recipients]
    for recipient in _dedupe(recipients):
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
            {"activity_code": spec.code, "recipients": _dedupe(recipients)},
        )


def cc_recipients_of(spec: CcTaskSpec, originator: str) -> list[str]:
    """静态解析接收人（校验器/测试用）。"""
    return _dedupe([originator if r == "originator" else r for r in spec.recipients])


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex


def _unused(*args: Any) -> None:  # pragma: no cover
    """占位以保持导入整洁。"""
    return None


_ = DomainEvent  # 事件名常量在 M3 mark_read 中使用
