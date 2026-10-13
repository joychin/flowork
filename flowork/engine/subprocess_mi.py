"""多实例子流程（v2.3 第四批 A9，multi-instance-call-design.md——Zeebe 契约）。

从 subprocess_support.py 拆出（单文件 ≤300 行红线）；共享件经延迟导入
（``_mapping_values``/``_children_of``）防环。契约三件套：
- fan-out：subjects 逐个解析＝每人一个子实例（``parent_index`` 记迭代序），
  输入映射按子求值并注入保留标识符 ``subject``（catalog 位点 mapping_input）；
- 汇聚：completion 公式（child_total/completed/child_failed 三计数）只做
  「提前收工」——命中即终止剩余 running 子（suspended 属管理员域不动），
  **不做失败机制**（C7 教训：提前完成丢失「谁反对」）；缺省 all＝全员结算；
- 聚合：collect＝输出按 parent_index 对齐聚合 list（未写 null 占位——下标
  稳定、完成序无关）；空 subjects＝零子立即完成（Zeebe skip 语义）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.engine.kernel import EngineExecutionError
from flowork.errors import DefinitionNotPublishedError
from flowork.events.domain import DomainEvent
from flowork.expr.cel import FormulaError, evaluate_formula_value
from flowork.runtime.lifecycle import FAILED_INSTANCE_STATES, InstanceState

if TYPE_CHECKING:
    from flowork.definitions.builtin import CallActivitySpec
    from flowork.engine.context import RuntimeNodeContext

__all__ = ["mi_finish", "start_mi_children"]


def start_mi_children(
    ctx: RuntimeNodeContext, spec: CallActivitySpec, row: Any, definition: Any
) -> None:
    """多实例 fan-out：每个 subject 一个子实例（parent_index 记迭代序）。"""
    from flowork.engine.subprocess_support import _mapping_values

    assert ctx.token is not None
    subjects = _mi_subjects(ctx, spec)
    for index, subject in enumerate(subjects):
        values = _mapping_values(
            "输入映射", spec.input_mappings or {}, ctx, ctx.instance, extra={"subject": subject}
        )
        _create_child(ctx, spec, row, definition, dict(values), parent_index=index)


def _mi_subjects(ctx: RuntimeNodeContext, spec: CallActivitySpec) -> list[str]:
    """解析 subjects（取人语义：查无传播；求值错→技术故障；A6 上限护栏同守）。"""
    from flowork.expr.context import expression_functions_of
    from flowork.participants.resolver import resolve_subjects
    from flowork.tasks.facts import attach_node, instance_facts, with_prev_task

    config = ctx.kernel.engine.config if ctx.kernel.engine else None
    assert ctx.instance is not None
    return resolve_subjects(
        list(spec.multi_instance.subjects),  # type: ignore[union-attr]
        with_prev_task(ctx.session, ctx.instance.id, ctx.variables or {}),
        ctx.instance.start_user,
        attach_node(expression_functions_of(config), ctx.session, ctx.instance.id),
        label=spec.code,
        instance=instance_facts(ctx.instance),
        now=ctx.kernel.config.now(),
    )


def _create_child(
    ctx: RuntimeNodeContext,
    spec: CallActivitySpec,
    row: Any,
    definition: Any,
    variables: dict[str, object],
    *,
    parent_index: int | None,
) -> None:
    """建一个 MI 子实例并激活（模拟传播同单子路径）。"""
    import uuid as _uuid

    from flowork.persistence.orm import ProcessInstanceRow

    assert ctx.instance is not None and ctx.token is not None
    child = ProcessInstanceRow(
        id=_uuid.uuid4().hex,
        definition_code=spec.process_code,
        definition_major=row.major,
        definition_revision=row.revision,
        definition_snapshot=row.content,
        state=InstanceState.RUNNING.value,
        started_at=ctx.kernel.config.now(),  # P1-11：业务时刻单源
        start_user=ctx.instance.start_user,
        variables=dict(variables),
        parent_id=ctx.instance.id,
        parent_token_id=ctx.token.id,
        simulated=ctx.instance.simulated,  # A10：模拟递归传播
        parent_index=parent_index,  # A9：index 对齐聚合的锚
    )
    ctx.session.add(child)
    ctx.session.flush()
    ctx.kernel.record_event(
        DomainEvent.PROCESS_STARTED,
        {"definition_code": spec.process_code, "parent_instance_id": ctx.instance.id},
        child.id,
    )
    ctx.kernel.record_event(
        DomainEvent.SUB_PROCESS_STARTED, {"sub_instance_id": child.id}, ctx.instance.id
    )
    start_node = definition.start_node()
    if start_node is None:
        raise DefinitionNotPublishedError(f"子流程 {spec.process_code} 缺少 start 节点")
    ctx.kernel.activate(ctx.session, child, start_node)


def mi_finish(ctx: RuntimeNodeContext, spec: CallActivitySpec, children: list[Any]) -> bool:
    """多实例汇聚：completion 判定 + collect 聚合（契约见模块 docstring）。"""
    from flowork.engine.subprocess_support import _mapping_values

    mi = spec.multi_instance
    assert mi is not None
    total = len(children)
    completed = sum(1 for child in children if child.state == InstanceState.COMPLETED.value)
    failed = sum(1 for child in children if child.state in FAILED_INSTANCE_STATES)
    if mi.completion != "all":
        if not _completion_hit(ctx, spec, mi.completion, total, completed, failed):
            return False
        _terminate_running_children(ctx, children)  # 提前收工：终止剩余 running
    elif completed + failed < total:
        return False  # all＝全员结算；paused/suspended 子阻塞（等待批语义）
    if mi.output_mode == "collect":
        _collect_outputs(ctx, spec, children, total)
    else:  # merge：validator 已拦 MI+merge，防御回退逐子覆写
        for child in children:
            if child.state == InstanceState.COMPLETED.value:
                outputs = _mapping_values("输出映射", spec.output_mappings or {}, ctx, child)
                for target, value in outputs.items():
                    ctx.variables[target] = value
    # input 契约边界③（code-review 2026-10 P1-17）：聚合写回后父变量全集仍须
    # 满足契约——与单子路径（sub_process_finished）同一条守卫，此前不对称
    import json

    from flowork.definitions.contract import validate_input_contract

    schema = (json.loads(ctx.instance.definition_snapshot) or {}).get("input")
    validate_input_contract(schema, dict(ctx.variables), boundary="node_output")
    return True


def _completion_hit(
    ctx: RuntimeNodeContext,
    spec: CallActivitySpec,
    formula: str,
    total: int,
    done: int,
    failed: int,
) -> bool:
    """completion 公式（三计数注入；错→技术故障）。"""
    from flowork.expr.context import expression_context, expression_functions_of
    from flowork.tasks.facts import instance_facts

    config = ctx.kernel.engine.config if ctx.kernel.engine else None
    # 父实例变量并入（code-review 2026-10 P1-8）：validator 按声明集放行
    # completion 位引用业务变量——装配面对齐，计数键冲突时计数优先
    counts = expression_context(
        {
            **dict(ctx.instance.variables or {}),
            "child_total": total,
            "child_completed": done,
            "child_failed": failed,
        },
        start_user=ctx.instance.start_user if ctx.instance else None,
        instance=instance_facts(ctx.instance) if ctx.instance else None,
        now=ctx.kernel.config.now(),
    )
    try:
        return bool(
            evaluate_formula_value(formula, counts, functions=expression_functions_of(config))
        )
    except FormulaError as exc:
        raise EngineExecutionError(
            f"多实例完成公式求值失败（{spec.code}：{formula!r}）：{exc}"
        ) from exc


def _collect_outputs(
    ctx: RuntimeNodeContext, spec: CallActivitySpec, children: list[Any], total: int
) -> None:
    """index 对齐聚合：completed 子的输出落 parent_index 位，未写保持 None。"""
    from flowork.engine.subprocess_support import _mapping_values

    collected: dict[str, list[object]] = {
        target: [None] * total for target in (spec.output_mappings or {})
    }
    for child in children:
        if child.state != InstanceState.COMPLETED.value or child.parent_index is None:
            continue
        outputs = _mapping_values("输出映射", spec.output_mappings or {}, ctx, child)
        for target, value in outputs.items():
            collected[target][child.parent_index] = value
    for target, values in collected.items():
        ctx.variables[target] = values


def _terminate_running_children(ctx: RuntimeNodeContext, children: list[Any]) -> None:
    """提前收工：终止剩余 running 子（撤令牌与待办，对齐 terminate_process 形态）。

    suspended/paused 子不终止（管理员域）。
    """
    from flowork.runtime.lifecycle import TokenState

    for child in children:
        if child.state != InstanceState.RUNNING.value:
            continue
        for token in ctx.kernel.tokens(
            ctx.session, child.id, states=(TokenState.UNFINISHED.value,)
        ):
            ctx.kernel.cancel_token(ctx.session, token)
        child.state = InstanceState.CANCELLED.value
        child.finished_at = ctx.kernel.config.now()
        ctx.kernel.record_event(
            DomainEvent.PROCESS_CANCELLED,
            {
                "reason": "multi_instance_completion",
                "parent_token": ctx.token.id if ctx.token else None,
            },
            child.id,
        )
    ctx.session.flush()
