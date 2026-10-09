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
    assumptions: Mapping[str, str] | None = None,
) -> DryRunResult:
    """按样例变量静态预演路由，返回路径步骤 / 警告 / 阻断。

    模拟面无实例：样例缺 ``initiator`` 时注入占位 ``u_initiator``、缺
    ``instance`` 时注入占位形状（id=i_preview，条件公式可引用保留标识符，
    与试算面一致）。

    Args:
        functions: 宿主 CEL 自定义函数表（与运行时同一注册来源，预演结果才可信）。
        assumptions: 等待节点预演假设（``{节点 code: "matched"|"elapsed"|"auto"}``，
            缺省 auto＝两种结果都预演：消息到达与超时各推一支，reason 标注假设）。
            这是预演假设不是运行时行为——运行时先到者决定走向，预演两面都画出来。
    """
    from flowork.clock import utc_now
    from flowork.expr.conditions import evaluate_condition

    variables = dict(variables)
    variables.setdefault("initiator", "u_initiator")  # 模拟面占位（见 docstring）
    variables.setdefault("now", utc_now())  # 与运行时同一注入（expression-dataface §2.1）
    variables.setdefault(
        "instance", {"id": "i_preview", "started_at": utc_now(), "business_key": None}
    )  # 模拟面无实例：占位形状（与运行时同一注入面）
    result = DryRunResult()
    nodes = {n.code: n for n in definition.nodes}
    start = next((n for n in definition.nodes if n.kind == "start"), None)
    if start is None:
        result.blocked.append("缺少 start 节点，无法预演")
        return result

    warned_conditions: set[str] = set()

    def _eval(flow: Any, extra: Mapping[str, Any] | None = None) -> bool:
        """求值一条边条件（CEL）；缺样例值/求值失败按不满足处理并告警一次。"""
        condition = flow.condition
        if condition is None:
            return True
        merged = {**variables, **extra} if extra else variables
        try:
            return evaluate_condition(condition, merged, functions=functions)
        except ConditionError as exc:
            text = _edge_text(flow) or "条件"
            if text not in warned_conditions:
                warned_conditions.add(text)
                result.warnings.append(
                    f"条件求值失败（{exc}；多为样例变量缺失），按不满足处理：{text}"
                )
        return False

    def _wait_shape(result_value: str, node: Any) -> dict[str, Any]:
        """wait.* 预演形状（运行时由引擎注入；message_id 在模拟面恒为 None）。"""
        from flowork.definitions.wait_node import wait_fields

        message, _timeout = wait_fields(node)
        return {
            "result": result_value,
            "message_name": message.name if message is not None else None,
            "message_id": None,
        }

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

        chosen: list[tuple[Any, str]] = []
        if node.kind == "wait":
            # 等待节点：出边条件引用 wait.*，模拟面按「假设」求值。
            # 缺省 auto＝消息到达 / 超时两种结果各推一支（竞速语义＝先到者决定走向，
            # 预演把两面都画出来）；显式假设只推一面。
            mode = (assumptions or {}).get(code, "auto")
            modes = (
                [("matched", "假设消息到达"), ("elapsed", "假设超时")]
                if mode == "auto"
                else [(mode, "假设消息到达" if mode == "matched" else "假设超时")]
            )
            for result_value, label in modes:
                shape = _wait_shape(result_value, node)
                for flow in conditional:
                    if _eval(flow, {"wait": shape}):
                        chosen.append((flow, f"{label}：{_edge_text(flow)}"))
            if not chosen and default is not None:
                default_reason = (
                    "两种假设下条件都不满足，走默认边" if mode == "auto" else "条件不满足，走默认边"
                )
                chosen = [(default, default_reason)]
            if not chosen and not outgoing:
                result.blocked.append(
                    f"等待节点 {code} 的出边条件在两种假设下都不满足且无默认边，流程在此无法路由"
                )
        else:
            passed = [f for f in conditional if _eval(f)]
            route_mode = getattr(node, "route_mode", "first_match")
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
