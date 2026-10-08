"""路由出口选择：节点出边的统一求值（基类=命中的都走，命中即走为分支特例）。

语义（对齐 对标产品 四类出口，节点级）：
- all_match（默认，基类与条件分支 route_mode=ALL_MATCH）：无条件边全部激活 +
  条件为真者激活；两者皆空时走默认边（is_default）兜底。
- first_match（条件分支 route_mode=FIRST_MATCH）：按声明顺序取第一条为真的条件边；
  全不中走默认边，再不中把"非默认无条件边"视为兜底；仍无 → RouteError。

CEL 词汇接线（spec §4.3/§9）：``functions`` 自 EngineConfig.expression_functions
拍平而来，可选透传至 expr.cel；``initiator`` 为求值保留标识符（P0-1 决策）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal

from flowork.engine.kernel import RouteError
from flowork.expr.conditions import evaluate_condition

if TYPE_CHECKING:
    from flowork.definitions.flow import SequenceFlow

__all__ = ["evaluation_variables", "expression_functions_of", "select_outgoing"]

Mode = Literal["all_match", "first_match"]


def evaluation_variables(instance: Any, variables: Mapping[str, Any]) -> dict[str, Any]:
    """构造求值上下文：变量副本 + ``initiator`` 保留标识符。

    显式声明同名变量时以声明为准（setdefault）；实例无 start_user 时缺省。
    """
    payload = dict(variables)
    start_user = getattr(instance, "start_user", None)
    if start_user:
        payload.setdefault("initiator", start_user)
    return payload


def expression_functions_of(config: Any) -> Mapping[str, Callable[..., object]] | None:
    """从 EngineConfig 拍平 CEL 函数表（端口 → {名: 可调用}）；未配置返回 None。"""
    port = getattr(config, "expression_functions", None)
    if port is None:
        return None
    return {name: registered.fn for name, registered in port.functions().items()}


def select_outgoing(
    outgoing: list[SequenceFlow],
    variables: dict[str, Any],
    mode: Mode = "all_match",
    functions: Mapping[str, Callable[..., object]] | None = None,
) -> list[SequenceFlow]:
    """按模式计算应激活的出边集合。"""
    unconditional = [flow for flow in outgoing if flow.condition is None]
    conditional = [flow for flow in outgoing if flow.condition is not None]
    defaults = [flow for flow in outgoing if flow.is_default]

    if not conditional:
        # 纯无条件出边（含 default 视同无条件）：全部激活（并行/直通）
        return unconditional if not defaults else list(unconditional)

    matched = _match_in_order(conditional, variables, functions)
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
) -> list[SequenceFlow]:
    """按声明顺序求值条件边，返回为真者。"""
    matched: list[SequenceFlow] = []
    for flow in conditional:
        assert flow.condition is not None  # conditional 分组已过滤
        if evaluate_condition(flow.condition, variables, functions=functions):
            matched.append(flow)
    return matched
