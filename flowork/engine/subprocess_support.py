"""子流程（CallActivity）支撑：父子实例链接与同步回写。

子实例在同一事务内内联启动（避免嵌套命令）；子实例完结时由
``resume_parent_if_waiting`` 回写父流程：完成父令牌并推进路由。
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from flowork.definitions.definition import load_definition
from flowork.errors import DefinitionNotPublishedError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.builtin import CallActivitySpec
    from flowork.engine.context import RuntimeNodeContext
    from flowork.engine.kernel import Kernel
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["resume_parent_if_waiting", "start_sub_process", "sub_process_finished"]


def start_sub_process(ctx: RuntimeNodeContext, spec: CallActivitySpec) -> None:
    """在同一事务内启动子流程实例（输入映射：子变量 ← 父变量）。"""
    assert ctx.token is not None
    from flowork.persistence.orm import ProcessDefinitionRow, ProcessInstanceRow

    row = ctx.session.scalars(
        select(ProcessDefinitionRow)
        .where(
            ProcessDefinitionRow.code == spec.process_code,
            ProcessDefinitionRow.status == "published",
        )
        .order_by(ProcessDefinitionRow.version.desc())
        .limit(1)
    ).first()
    if row is None:
        raise DefinitionNotPublishedError(f"子流程定义未发布：{spec.process_code}")
    definition = load_definition(json.loads(row.content), ctx.kernel.registry)
    input_variables = {
        target: ctx.variables.get(source) for target, source in spec.input_mappings.items()
    }
    child = ProcessInstanceRow(
        id=uuid.uuid4().hex,
        definition_code=spec.process_code,
        definition_version=row.version,
        definition_snapshot=row.content,
        state="running",
        start_user=ctx.instance.start_user,
        variables=dict(input_variables),
        parent_id=ctx.instance.id,
        parent_token_id=ctx.token.id,
    )
    ctx.session.add(child)
    ctx.session.flush()
    ctx.kernel.record_event(
        "process_started",
        {"definition_code": spec.process_code, "parent_instance_id": ctx.instance.id},
        child.id,
    )
    ctx.kernel.record_event("sub_process_started", {"sub_instance_id": child.id}, ctx.instance.id)
    start_node = definition.start_node()
    if start_node is None:
        raise DefinitionNotPublishedError(f"子流程 {spec.process_code} 缺少 start 节点")
    ctx.kernel.activate(ctx.session, child, start_node)
    ctx.info(f"子流程 {spec.process_code} 已启动：{child.id[:8]}")


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
    kernel.advance_after_task(session, parent, token)


def _children_of(
    session: Session, parent_instance_id: str, parent_token_id: str
) -> list[ProcessInstanceRow]:
    from flowork.persistence.orm import ProcessInstanceRow as _InstanceRow

    stmt = select(_InstanceRow).where(
        _InstanceRow.parent_id == parent_instance_id,
        _InstanceRow.parent_token_id == parent_token_id,
    )
    return list(session.scalars(stmt))
