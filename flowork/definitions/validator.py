"""设计时校验器：结构 / 可达性 / 网关 / 参与者 / kind 注册。

不抛异常：返回 ``list[ValidateIssue]``，由调用方（CLI/发布服务）决定渲染与拦截；
存在 ERROR 级问题时禁止发布（``assert_valid``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from flowork.definitions.builtin import (
    KIND_CC_TASK,
    KIND_END,
    KIND_GATEWAY,
    KIND_START,
    KIND_USER_TASK,
    GatewayKind,
)
from flowork.definitions.flow import MAX_FORMULA_LENGTH
from flowork.errors import DefinitionError

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition
    from flowork.runtime.registry import NodeRegistry

__all__ = [
    "InvalidDefinition",
    "IssueLevel",
    "ValidateIssue",
    "assert_valid",
    "validate_definition",
]


class IssueLevel(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidateIssue:
    """一条校验结果：level + 规则编码 + 定位（节点/连线）+ 说明。"""

    level: IssueLevel
    rule: str
    message: str
    node: str | None = None
    flow: str | None = None

    def __str__(self) -> str:
        where = f" 节点 {self.node}" if self.node else ""
        where += f" 连线 {self.flow}" if self.flow else ""
        return f"[{self.level.value}] {self.rule}{where}: {self.message}"


class InvalidDefinition(DefinitionError):
    """定义存在 ERROR 级校验问题。"""

    def __init__(self, issues: list[ValidateIssue]) -> None:
        self.issues = issues
        detail = "\n".join(f"  {issue}" for issue in issues)
        super().__init__(f"流程定义校验未通过（{len(issues)} 个错误）：\n{detail}")


def validate_definition(
    definition: ProcessDefinition, registry: NodeRegistry
) -> list[ValidateIssue]:
    """执行全部设计时校验，返回问题列表（ERROR + WARNING）。"""
    issues: list[ValidateIssue] = []
    nodes = {n.code: n for n in definition.nodes}
    flows = definition.flows

    def error(rule: str, message: str, **where: str) -> None:
        issues.append(ValidateIssue(IssueLevel.ERROR, rule, message, **where))

    def warning(rule: str, message: str, **where: str) -> None:
        issues.append(ValidateIssue(IssueLevel.WARNING, rule, message, **where))

    # —— 唯一性与编码 ——
    seen: set[str] = set()
    for node in definition.nodes:
        if not node.code:
            error("node.code.empty", "节点编码为空")
        if node.code in seen:
            error("node.code.duplicate", f"节点编码重复：{node.code}", node=node.code)
        seen.add(node.code)

    # —— kind 注册（G2：未知 kind 设计期拦截）——
    for node in definition.nodes:
        if node.kind not in registry:
            error(
                "node.kind.unregistered",
                f"未注册的节点类型 kind={node.kind!r}（已注册：{', '.join(registry.kinds())}）",
                node=node.code,
            )

    # —— start/end 结构 ——
    starts = [n for n in definition.nodes if n.kind == KIND_START]
    ends = [n for n in definition.nodes if n.kind == KIND_END]
    if len(starts) != 1:
        error("structure.start.count", f"必须有且仅有一个 start 节点，当前 {len(starts)} 个")
    if not ends:
        error("structure.end.missing", "至少需要一个 end 节点")

    for node in definition.nodes:
        if node.kind == KIND_START and definition.incoming(node.code):
            error("structure.start.incoming", "start 节点不允许有入边", node=node.code)
        if node.kind == KIND_END and definition.outgoing(node.code):
            error("structure.end.outgoing", "end 节点不允许有出边", node=node.code)

    # —— 连线端点存在性 ——
    for flow in flows:
        for end, role in ((flow.source, "source"), (flow.target, "target")):
            if end not in nodes:
                error(
                    "flow.endpoint.missing",
                    f"连线 {role} 节点不存在：{end}",
                    flow=flow.flow_code(),
                )

    # —— 入边/出边完备性 ——
    for node in definition.nodes:
        if node.kind == KIND_START:
            continue
        if not definition.incoming(node.code):
            warning("structure.node.unreachable", "节点不可达（无入边）", node=node.code)
        if node.kind != KIND_END and not definition.outgoing(node.code):
            error("structure.node.deadend", "非 end 节点必须有出边", node=node.code)

    # —— 从 start 出发的可达性 ——
    if starts:
        reachable: set[str] = set()
        queue = [starts[0].code]
        while queue:
            current = queue.pop()
            if current in reachable:
                continue
            reachable.add(current)
            queue.extend(f.target for f in definition.outgoing(current) if f.target in nodes)
        for node in definition.nodes:
            if node.code not in reachable:
                warning("structure.node.unreachable", "节点从 start 不可达", node=node.code)

    # —— 网关规则 ——
    for node in definition.nodes:
        if node.kind != KIND_GATEWAY:
            continue
        gateway_kind = getattr(node, "gateway_kind", GatewayKind.EXCLUSIVE)
        outgoing = definition.outgoing(node.code)
        defaults = [f for f in outgoing if f.is_default]
        conditional = [f for f in outgoing if f.condition is not None]
        if len(outgoing) < 2:
            warning("gateway.single_outgoing", "网关出边少于 2 条，仅作透传", node=node.code)
        if gateway_kind == GatewayKind.EXCLUSIVE:
            if len(defaults) > 1:
                error("gateway.default.duplicate", "排他网关最多一条默认边", node=node.code)
            if len(outgoing) > 1 and not defaults and len(conditional) < len(outgoing):
                error(
                    "gateway.exclusive.no_default",
                    "排他网关存在无条件出边且未设置默认边（条件全不满足时无法路由）",
                    node=node.code,
                )
            for flow in outgoing:
                if flow.is_default and flow.condition is not None:
                    error(
                        "gateway.default.has_condition",
                        "默认边不允许再挂条件",
                        node=node.code,
                        flow=flow.flow_code(),
                    )
            if outgoing and not defaults and conditional and len(conditional) == len(outgoing):
                warning(
                    "gateway.exclusive.all_conditional",
                    "排他网关出边全部带条件且无默认边：条件不互补时运行期将无法路由",
                    node=node.code,
                )
        elif gateway_kind == GatewayKind.INCLUSIVE:
            # 相容网关：条件边 + 可选默认边；无条件非默认边视为告警（恒真分支）
            if len(defaults) > 1:
                error("gateway.default.duplicate", "网关最多一条默认边", node=node.code)
            for flow in outgoing:
                if flow.condition is None and not flow.is_default:
                    warning(
                        "gateway.inclusive.unconditional",
                        "相容网关的无条件出边恒为真（等价并行分支）",
                        node=node.code,
                        flow=flow.flow_code(),
                    )
        else:  # PARALLEL
            if defaults:
                error(
                    "gateway.default.not_allowed",
                    "并行网关不允许默认边（默认边仅用于排他/相容网关）",
                    node=node.code,
                )
            for flow in outgoing:
                if flow.condition is not None:
                    error(
                        "gateway.unconditional_only",
                        "并行网关出边不允许挂条件",
                        node=node.code,
                        flow=flow.flow_code(),
                    )

    # —— 节点语义检查 ——
    for node in definition.nodes:
        if node.kind == KIND_USER_TASK:
            has_participant = any(
                (
                    getattr(node, "assignee", None),
                    getattr(node, "assignees", None),
                    getattr(node, "candidate_users", None),
                    getattr(node, "candidate_groups", None),
                )
            )
            if not has_participant:
                warning("user_task.no_participant", "审批节点未配置任何参与者", node=node.code)
            reject_target = getattr(node, "reject_target", None)
            if reject_target and reject_target not in nodes:
                error(
                    "user_task.reject_target.missing",
                    f"驳回目标节点不存在：{reject_target}",
                    node=node.code,
                )
        if node.kind == KIND_CC_TASK and not getattr(node, "recipients", None):
            warning("cc_task.no_recipients", "传阅节点未配置接收人", node=node.code)

    # —— 条件合法性 ——
    for flow in flows:
        condition = flow.condition
        if condition is None:
            continue
        if condition.groups and condition.formula:
            error(
                "condition.both_set",
                "条件组与公式只允许其一（条件组优先，公式易被忽略）",
                flow=flow.flow_code(),
            )
        if condition.formula and len(condition.formula) > MAX_FORMULA_LENGTH:
            error(
                "condition.formula.too_long",
                f"公式长度 {len(condition.formula)} 超过上限 {MAX_FORMULA_LENGTH}",
                flow=flow.flow_code(),
            )
        if not condition.groups and not condition.formula:
            error(
                "condition.empty",
                "条件为空（groups 与 formula 均未设置）",
                flow=flow.flow_code(),
            )
        if not flow.is_default and not condition and definition.outgoing(flow.source):
            source_node = nodes.get(flow.source)
            source_is_gateway = source_node is not None and source_node.kind == KIND_GATEWAY
            if source_is_gateway:
                warning(
                    "condition.missing_on_gateway",
                    "网关出边未挂条件（排他网关下仅默认边允许无条件）",
                    flow=flow.flow_code(),
                )

    return issues


def assert_valid(definition: ProcessDefinition, registry: NodeRegistry) -> None:
    """存在 ERROR 级问题时抛 InvalidDefinition（发布前调用）。"""
    issues = [
        issue
        for issue in validate_definition(definition, registry)
        if issue.level == IssueLevel.ERROR
    ]
    if issues:
        raise InvalidDefinition(issues)
