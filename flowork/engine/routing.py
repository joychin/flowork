"""路由出口选择：节点出边的统一求值（基类=相容语义，排他网关特例）。

语义（对齐《节点抽象与扩展设计》四类出口，节点级）：
- inclusive（默认，基类与 INCLUSIVE 网关）：无条件边全部激活 + 条件为真者激活；
  两者皆空时走默认边（is_default）兜底。
- exclusive（EXCLUSIVE 网关）：按声明顺序取第一条为真的条件边；全不中走默认边，
  再不中把"非默认无条件边"视为兜底；仍无 → RouteError。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from flowork.engine.kernel import RouteError
from flowork.expr.conditions import evaluate_condition

if TYPE_CHECKING:
    from flowork.definitions.flow import SequenceFlow

__all__ = ["select_outgoing"]

Mode = Literal["inclusive", "exclusive"]


def select_outgoing(
    outgoing: list[SequenceFlow], variables: dict[str, Any], mode: Mode = "inclusive"
) -> list[SequenceFlow]:
    """按模式计算应激活的出边集合。"""
    unconditional = [flow for flow in outgoing if flow.condition is None]
    conditional = [flow for flow in outgoing if flow.condition is not None]
    defaults = [flow for flow in outgoing if flow.is_default]

    if not conditional:
        # 纯无条件出边（含 default 视同无条件）：全部激活（并行/直通）
        return unconditional if not defaults else list(unconditional)

    matched = _match_in_order(conditional, variables)
    if mode == "exclusive":
        if matched:
            return matched[:1]
        if defaults:
            return defaults
        fallback = [flow for flow in unconditional if not flow.is_default]
        if fallback:
            return fallback
        raise RouteError("排他路由无匹配分支且未配置默认边")

    # inclusive：无条件（非默认）+ 条件真者；皆空走默认边
    plain = [flow for flow in unconditional if not flow.is_default]
    selected = plain + matched
    if selected:
        return selected
    if defaults:
        return defaults
    raise RouteError("路由无匹配分支且未配置默认边")


def _match_in_order(
    conditional: list[SequenceFlow], variables: dict[str, Any]
) -> list[SequenceFlow]:
    """按声明顺序求值条件边，返回为真者。"""
    matched: list[SequenceFlow] = []
    for flow in conditional:
        assert flow.condition is not None  # conditional 分组已过滤
        if evaluate_condition(flow.condition, variables):
            matched.append(flow)
    return matched
