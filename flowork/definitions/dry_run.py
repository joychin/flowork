"""设计时路由预演（dry-run）：静态模拟条件求值，画出「这套变量会走出哪条路」。

不落库、不执行节点行为、不产生任务——只做图路由：
无条件出边恒通行；条件出边按 ``expr.conditions`` 对样例变量求值；
分支节点 route_mode first_match 只取第一条命中边；全不中走默认边；
没有任何可通行出边时记录 blocked。循环图按节点去重 + 步数上限兜底。

与运行时的差异（诚实边界）：本模块回答「路由会怎么走」，不回答
「任务谁办、动作做没做成功」——那是引擎试运行的职责。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from flowork.errors import ConditionError

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition

__all__ = [
    "MAX_STEPS",
    "DryRunResult",
    "DryRunStep",
    "simulate_route",
]

MAX_STEPS = 500


@dataclass(frozen=True)
class DryRunStep:
    """预演路径上的一步：到达的节点 + 经由哪条出边 + 命中原因。"""

    node: str
    via_source: str | None = None  # 首步(start)为 None
    via_reason: str = ""
    note: str = ""  # 汇聚/并行等补充说明


@dataclass(frozen=True)
class DryRunResult:
    steps: list[DryRunStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocked


def _edge_text(flow: Any) -> str:
    """一条边的条件摘要（单轨 CEL：公式原文）。"""
    return str(flow.condition) if flow.condition else ""


def simulate_route(
    definition: ProcessDefinition,
    variables: Mapping[str, Any],
    *,
    functions: Mapping[str, Any] | None = None,
) -> DryRunResult:
    """按样例变量静态预演路由，返回路径步骤 / 警告 / 阻断。

    Args:
        functions: 宿主 CEL 自定义函数表（与运行时同一注册来源，预演结果才可信）。
    """
    from flowork.expr.conditions import evaluate_condition

    result = DryRunResult()
    nodes = {n.code: n for n in definition.nodes}
    start = next((n for n in definition.nodes if n.kind == "start"), None)
    if start is None:
        result.blocked.append("缺少 start 节点，无法预演")
        return result

    warned_conditions: set[str] = set()

    def _eval(flow: Any) -> bool:
        """求值一条边条件（CEL）；缺样例值/求值失败按不满足处理并告警一次。"""
        condition = flow.condition
        if condition is None:
            return True
        try:
            return evaluate_condition(condition, variables, functions=functions)
        except ConditionError as exc:
            text = _edge_text(flow) or "条件"
            if text not in warned_conditions:
                warned_conditions.add(text)
                result.warnings.append(
                    f"条件求值失败（{exc}；多为样例变量缺失），按不满足处理：{text}"
                )
        return False

    queue: list[tuple[str, str | None, str, str]] = [(start.code, None, "", "")]
    visited: set[str] = set()
    truncated = False

    while queue:
        if len(visited) >= MAX_STEPS:
            truncated = True
            break
        code, via_source, via_reason, note = queue.pop(0)
        if code in visited or code not in nodes:
            continue
        visited.add(code)
        result.steps.append(
            DryRunStep(node=code, via_source=via_source, via_reason=via_reason, note=note)
        )

        node = nodes[code]
        outgoing = definition.outgoing(code)
        if not outgoing:
            if node.kind != "end":
                result.blocked.append(f"节点 {code} 无出边（非 end 节点死端）")
            continue

        unconditional = [f for f in outgoing if not f.is_default and f.condition is None]
        conditional = [f for f in outgoing if not f.is_default and f.condition is not None]
        defaults = [f for f in outgoing if f.is_default]
        default = defaults[0] if defaults else None

        passed = [f for f in conditional if _eval(f)]
        route_mode = getattr(node, "route_mode", "first_match")

        chosen: list[tuple[Any, str]] = []
        if node.kind == "branch" and route_mode == "first_match":
            if passed:
                first = passed[0]
                chosen = [(first, f"命中即走：{_edge_text(first)}")]
            elif default is not None:
                chosen = [(default, "条件全不满足，走默认边")]
            elif unconditional:
                chosen = [(f, "无条件出边") for f in unconditional]
                result.warnings.append(
                    f"分支 {code} 条件全不满足且无默认边，"
                    "当前仅有无条件出边（命中即走分支建议设置默认边）"
                )
            else:
                result.blocked.append(f"分支 {code} 条件全不满足且无默认边，流程在此无法路由")
        else:
            chosen = [(f, "无条件出边") for f in unconditional]
            chosen += [(f, f"条件满足：{_edge_text(f)}") for f in passed]
            if not chosen and default is not None:
                chosen = [(default, "条件全不满足，走默认边")]
            if not chosen and outgoing:
                result.blocked.append(
                    f"节点 {code} 的出边条件全部不满足且无默认边，流程在此无法路由"
                )

        multi = len(chosen) > 1
        if multi:
            chosen = [
                (flow, "无条件出边（同时进行）" if reason == "无条件出边" else reason)
                for flow, reason in chosen
            ]
        for flow, reason in chosen:
            target = flow.target
            extra = "并行分支" if multi else ""
            if multi and definition.incoming(target) and len(definition.incoming(target)) >= 2:
                extra = "并行分支（汇合于 " + target + "，按其开始条件推进）"
            queue.append((target, code, reason, extra))

    if truncated:
        result.warnings.append(f"路径超过 {MAX_STEPS} 步上限，预演已截断（图中可能存在循环）")
    return result
