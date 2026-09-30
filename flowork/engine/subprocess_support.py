"""子流程（CallActivity）支撑：父子实例链接与同步回写。

子实例记录 parent_id + parent_token_id；子实例完结时由
``resume_parent_if_waiting`` 回写父流程：完成父令牌并推进路由。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.events.domain import DomainEvent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.builtin import CallActivitySpec
    from flowork.engine.context import RuntimeNodeContext
    from flowork.engine.kernel import Kernel
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["resume_parent_if_waiting", "start_sub_process", "sub_process_finished"]


def start_sub_process(ctx: RuntimeNodeContext, spec: CallActivitySpec) -> None:
    """同步启动子流程实例（输入映射：子变量 ← 父变量）。"""
    assert ctx.token is not None
    input_variables = {
        target: ctx.variables.get(source) for target, source in spec.input_mappings.items()
    }
    sub_instance_id = ctx.kernel.engine.runtime._start_child(
        code=spec.process_code,
        start_user=ctx.instance.start_user,
        variables=input_variables,
        parent_instance_id=ctx.instance.id,
        parent_token_id=ctx.token.id,
    )
    ctx.info(f"子流程 {spec.process_code} 已启动：{sub_instance_id}")
    ctx.emit("sub_process_started", {"sub_instance_id": sub_instance_id})


def sub_process_finished(ctx: RuntimeNodeContext, spec: CallActivitySpec) -> bool:
    """同步子流程：全部子实例完结时父节点完成（输出映射写回父变量）。"""
    assert ctx.token is not None
    children = _children_of(ctx.session, ctx.instance.id, ctx.token.id)
    if any(child.state == "running" for child in children):
        return False
    for child in children:
        if child.state == "completed":
            for target, source in spec.output_mappings.items():
                ctx.variables[target] = (child.variables or {}).get(source)
    return True


def resume_parent_if_waiting(kernel: Kernel, session: Session, child: ProcessInstanceRow) -> None:
    """子实例完结后，唤醒等待中的父 CallActivity 令牌。"""
    if not child.parent_id or not child.parent_token_id:
        return
    from flowork.persistence.orm import ProcessInstanceRow as _InstanceRow
    from flowork.persistence.orm import TokenRow

    parent = session.get(_InstanceRow, child.parent_id)
    if parent is None or parent.state != "running":
        return
    token = session.get(TokenRow, child.parent_token_id)
    if token is None or token.state != "unfinished":
        return
    kernel.record_event(DomainEvent.NODE_EXITED, {"activity_code": token.activity_code}, parent.id)
    kernel.advance_after_task(session, parent, token)


def _children_of(
    session: Session, parent_instance_id: str, parent_token_id: str
) -> list[ProcessInstanceRow]:
    from sqlalchemy import select

    from flowork.persistence.orm import ProcessInstanceRow as _InstanceRow

    stmt = select(_InstanceRow).where(
        _InstanceRow.parent_id == parent_instance_id,
        _InstanceRow.parent_token_id == parent_token_id,
    )
    return list(session.scalars(stmt))
