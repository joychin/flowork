"""路由出口选择：节点出边的统一求值（基类=命中的都走，命中即走为分支特例）。

语义（对齐行业四类出口，节点级）：
- all_match（默认，基类与条件分支 route_mode=ALL_MATCH）：无条件边全部激活 +
  条件为真者激活；两者皆空时走默认边（is_default）兜底。
- first_match（条件分支 route_mode=FIRST_MATCH）：按声明顺序取第一条为真的条件边；
  全不中走默认边，再不中把"非默认无条件边"视为兜底；仍无 → RouteError。

CEL 词汇接线：``functions`` 自 EngineConfig.expression_functions
拍平而来（expr.context.expression_functions_of），可选透传至 expr.cel；
``initiator`` 为求值保留标识符。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from flowork.engine.kernel import EngineExecutionError, RouteError
from flowork.engine.route_audit import RoutingAudit, snapshot_of
from flowork.expr.cel import FormulaError, references_identifier
from flowork.expr.conditions import evaluate_condition
from flowork.expr.context import expression_context

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.flow import SequenceFlow

__all__ = ["evaluation_variables", "select_outgoing"]

Mode = Literal["all_match", "first_match"]


def evaluation_variables(
    instance: Any,
    variables: Mapping[str, Any],
    *,
    session: Session | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """构造求值上下文（expr.context.expression_context 的实例版薄包装）。

    显式声明同名变量时以声明为准（setdefault）；实例无 start_user 时缺省。
    带 session 时注入 ``prev_task`` 上一站快照（
    查无＝不注入，引用预检在 select_outgoing 内温和跳过）。
    ``instance`` 全局事实（id/started_at/business_key）恒注入（全位点）。
    ``now`` 必须由引擎位点显式传入（``EngineConfig.now``——时钟统一：
    FakeClock 下条件求值与任务时限同源）；缺省墙钟仅供无引擎装配面。
    """
    prev_task = None
    if session is not None:
        from flowork.tasks.facts import prev_task_snapshot

        prev_task = prev_task_snapshot(session, instance.id)
    from flowork.tasks.facts import instance_facts

    return expression_context(
        variables,
        start_user=getattr(instance, "start_user", None),
        prev_task=prev_task,
        instance=instance_facts(instance),
        now=now,
    )


def select_outgoing(
    outgoing: list[SequenceFlow],
    variables: dict[str, Any],
    mode: Mode = "all_match",
    functions: Mapping[str, Callable[..., object]] | None = None,
    *,
    audit: RoutingAudit | None = None,
) -> list[SequenceFlow]:
    """按模式计算应激活的出边集合；带 audit 时旁路记录本次决策。"""
    selected = _select(outgoing, variables, mode, functions, audit)
    if audit is not None:
        audit.source = outgoing[0].source if outgoing else ""
        audit.mode = mode
        # 累计而非覆盖：同令牌多段路由（如 incident skip 的两跳手术）
        # 要在一行决策里呈现全部择道；单段调用位点行为不变
        audit.selected.extend(flow.flow_code() for flow in selected)
        audit.snapshot = snapshot_of(variables, audit.node_calls)
    return selected


def _select(
    outgoing: list[SequenceFlow],
    variables: dict[str, Any],
    mode: Mode,
    functions: Mapping[str, Callable[..., object]] | None,
    audit: RoutingAudit | None,
) -> list[SequenceFlow]:
    unconditional = [flow for flow in outgoing if flow.condition is None]
    conditional = [flow for flow in outgoing if flow.condition is not None]
    defaults = [flow for flow in outgoing if flow.is_default]

    if not conditional:
        # 纯无条件出边（含 default 视同无条件）：全部激活（并行/直通）——
        # default ⊆ unconditional，无需分支（2026-10-21 内核 review P2：死三元）
        return list(unconditional)

    matched = _match_in_order(conditional, variables, functions, audit)
    if mode == "first_match":
        if matched:
            return matched[:1]
        if defaults:
            return defaults
        fallback = [flow for flow in unconditional if not flow.is_default]
        if fallback:
            return fallback
        raise RouteError("命中即走路由无匹配分支且未配置默认边")

    # all_match：无条件（非默认）+ 条件真者；皆空走默认边
    plain = [flow for flow in unconditional if not flow.is_default]
    selected = plain + matched
    if selected:
        return selected
    if defaults:
        return defaults
    raise RouteError("路由无匹配分支且未配置默认边")


def _match_in_order(
    conditional: list[SequenceFlow],
    variables: dict[str, Any],
    functions: Mapping[str, Callable[..., object]] | None = None,
    audit: RoutingAudit | None = None,
) -> list[SequenceFlow]:
    """按声明顺序求值条件边（单轨 CEL），返回为真者。

    求值错（FormulaError）按错误二分归**技术故障**：包成
    EngineExecutionError → 命令管线挂起实例 + 故障单。
    """
    matched: list[SequenceFlow] = []
    for flow in conditional:
        assert flow.condition is not None  # conditional 分组已过滤
        if "prev_task" not in variables and references_identifier(flow.condition, "prev_task"):
            if audit is not None:
                audit.record_edge(flow.flow_code(), flow.condition, "no_fact")
            continue  # 上一站查无：条件按不满足（温和跳过）
        try:
            hit = evaluate_condition(flow.condition, variables, functions=functions)
        except FormulaError as exc:
            raise EngineExecutionError(
                f"连线条件公式求值失败（{flow.flow_code()}）：{exc}"
            ) from exc
        if audit is not None:
            audit.record_edge(flow.flow_code(), flow.condition, "true" if hit else "false")
        if hit:
            matched.append(flow)
    return matched
