"""子流程（CallActivity）支撑：父子实例链接与同步回写。

子实例在同一事务内内联启动（避免嵌套命令）；子实例完结时由
``resume_parent_if_waiting`` 回写父流程：完成父令牌并推进路由。
映射值为 **CEL 表达式**（spec §4.2）：输入映射在父上下文求值，输出映射在
子上下文求值；裸变量名是 CEL 子集（同名直传零迁移）。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from flowork.definitions.definition import load_definition
from flowork.engine.kernel import EngineExecutionError
from flowork.errors import DefinitionNotPublishedError
from flowork.expr.cel import FormulaError, evaluate_formula_value
from flowork.expr.context import expression_context, expression_functions_of

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.builtin import CallActivitySpec
    from flowork.engine.context import RuntimeNodeContext
    from flowork.engine.kernel import Kernel
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["resume_parent_if_waiting", "start_sub_process", "sub_process_finished"]


_BARE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 子实例「已结算」= 终态；running/paused/suspended 一律未结算（回归：原只挡 running，
# 暂停/挂起中的子实例被当已结算，父侧无输出映射直通）。cancelled/failed 算结算：
# 管理员修复/强杀场景（对齐 _mapping_values 的缺键 None 语义）。
_CHILD_SETTLED_STATES = frozenset({"completed", "terminated", "cancelled", "failed"})


def _mapping_values(
    where: str,
    mappings: dict[str, str],
    ctx: RuntimeNodeContext,
    owner: ProcessInstanceRow,
    *,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    """映射右侧逐条 CEL 求值（owner 提供变量与 initiator；错→技术故障）。

    裸标识符直传且键缺失 → None（数据缺失≠求值错：子实例被管理员修复直达
    终态等场景，对齐旧 ``.get`` 直传语义）；其余表达式（运算/函数/字段选择）严格。
    extra＝额外注入标识符（A9 多实例：当前迭代的 ``subject``）。
    """
    config = ctx.kernel.engine.config if ctx.kernel.engine else None
    from flowork.tasks.facts import instance_facts

    variables = expression_context(
        owner.variables or {},
        start_user=owner.start_user,
        instance=instance_facts(owner),
        now=ctx.kernel.config.now(),
    )
    if extra:
        variables = {**variables, **extra}
    values: dict[str, object] = {}
    for target, expression in mappings.items():
        if _BARE_IDENT.match(expression) and expression not in variables:
            values[target] = None
            continue
        try:
            values[target] = evaluate_formula_value(
                expression, variables, functions=expression_functions_of(config)
            )
        except FormulaError as exc:
            raise EngineExecutionError(
                f"子流程{where}求值失败（{expression!r} → {target}）：{exc}"
            ) from exc
    return values


def start_sub_process(ctx: RuntimeNodeContext, spec: CallActivitySpec) -> None:
    """在同一事务内启动子流程实例（输入映射：子变量 ← 父上下文求值）。

    多实例（A9）：subjects 逐个解析＝每人一个子实例（index 记 parent_index），
    输入映射按子求值并注入 ``subject``；空集合＝零子实例（sub_process_finished
    立即放行＝Zeebe skip 语义）。
    """
    assert ctx.token is not None
    from flowork.persistence.orm import ProcessDefinitionRow, ProcessInstanceRow

    row = ctx.session.scalars(
        select(ProcessDefinitionRow)
        .where(
            ProcessDefinitionRow.code == spec.process_code,
            ProcessDefinitionRow.status == "published",
        )
        .order_by(ProcessDefinitionRow.major.desc(), ProcessDefinitionRow.revision.desc())
        .limit(1)
    ).first()
    if row is None:
        raise DefinitionNotPublishedError(f"子流程定义未发布：{spec.process_code}")
    definition = load_definition(json.loads(row.content), ctx.kernel.registry)
    assert ctx.instance is not None
    if spec.multi_instance is not None:
        from flowork.engine.subprocess_mi import start_mi_children

        start_mi_children(ctx, spec, row, definition)
        return
    input_variables = _mapping_values("输入映射", spec.input_mappings, ctx, ctx.instance)
    child = ProcessInstanceRow(
        id=uuid.uuid4().hex,
        definition_code=spec.process_code,
        definition_major=row.major,
        definition_revision=row.revision,
        definition_snapshot=row.content,
        state="running",
        started_at=ctx.kernel.config.now(),  # P1-11：业务时刻单源
        start_user=ctx.instance.start_user,
        variables=dict(input_variables),
        parent_id=ctx.instance.id,
        parent_token_id=ctx.token.id,
        simulated=ctx.instance.simulated,  # v2.3 A10：模拟递归传播
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
    """同步子流程：全部子实例结算后父节点完成（输出映射写回父变量）。

    结算=终态（completed/terminated/cancelled/failed）；paused/suspended 的
    子实例阻塞父侧——多子场景下其余子完成时，父不得绕过暂停中的兄弟子直通。
    """
    assert ctx.token is not None
    children = _children_of(ctx.session, ctx.instance.id, ctx.token.id)
    if spec.multi_instance is not None:
        from flowork.engine.subprocess_mi import mi_finish

        return mi_finish(ctx, spec, children)
    if any(child.state not in _CHILD_SETTLED_STATES for child in children):
        return False
    for child in children:
        if child.state == "completed":
            outputs = _mapping_values("输出映射", spec.output_mappings, ctx, child)
            for target, value in outputs.items():
                ctx.variables[target] = value
    # input 契约边界③：节点输出映射后父变量全集仍须满足契约（spec §3.2）
    import json

    from flowork.definitions.contract import validate_input_contract

    schema = (json.loads(ctx.instance.definition_snapshot) or {}).get("input")
    validate_input_contract(schema, dict(ctx.variables), boundary="node_output")
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
