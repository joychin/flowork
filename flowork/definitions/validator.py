"""设计时校验器：结构 / 可达性 / 条件分支 / 开始条件 / 参与者 / kind 注册。

不抛异常：返回 ``list[ValidateIssue]``，由调用方（CLI/发布服务）决定渲染与拦截；
存在 ERROR 级问题时禁止发布（``assert_valid``）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from flowork.definitions.builtin import (
    KIND_BRANCH,
    KIND_CC_TASK,
    KIND_END,
    KIND_START,
    KIND_USER_TASK,
    RouteMode,
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

# 公式白名单符号 + Python 关键字：变量引用检查时不算「未知变量」
_FORMULA_NON_VARS = frozenset(
    {
        "abs",
        "min",
        "max",
        "round",
        "len",
        "sum",
        "str",
        "int",
        "float",
        "bool",
        "True",
        "False",
        "None",
        "and",
        "or",
        "not",
        "in",
        "is",
        "if",
        "else",
    }
)


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
    definition: ProcessDefinition,
    registry: NodeRegistry,
    *,
    org_configured: bool | None = None,
) -> list[ValidateIssue]:
    """执行全部设计时校验，返回问题列表（ERROR + WARNING）。

    Args:
        org_configured: 调用方是否知道组织目录已配置（spec §9）。
            None＝未知（跳过组主体检查）；False＝确知未配置——图纸使用
            role:/org: 组主体时给 WARNING（不拦发布，后果＝组待办无人能认领）。
    """
    issues: list[ValidateIssue] = []
    nodes = {n.code: n for n in definition.nodes}
    flows = definition.flows

    def error(rule: str, message: str, **where: str) -> None:
        issues.append(ValidateIssue(IssueLevel.ERROR, rule, message, **where))

    def warning(rule: str, message: str, **where: str) -> None:
        issues.append(ValidateIssue(IssueLevel.WARNING, rule, message, **where))

    for variable in definition.variables:
        if variable.searchable and variable.type not in ("string", "number", "boolean"):
            error(
                "variables.searchable_type",
                f"searchable 变量仅限 string/number/boolean（{variable.name}: {variable.type}）",
                node=variable.name,
            )

    if org_configured is False:
        for node in definition.nodes:
            groups = list(getattr(node, "candidate_groups", None) or [])
            prefixed = [g for g in groups if g.startswith(("role:", "org:"))]
            if prefixed:
                warning(
                    "participant.group_without_directory",
                    f"图纸使用组主体 {prefixed} 而组织目录未配置：组待办将无人能认领"
                    "（EngineConfig.org / 目录见 spec §9）",
                    node=node.code,
                )

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

    # —— 条件分支规则 ——
    for node in definition.nodes:
        if node.kind != KIND_BRANCH:
            continue
        route_mode = getattr(node, "route_mode", RouteMode.FIRST_MATCH)
        outgoing = definition.outgoing(node.code)
        defaults = [f for f in outgoing if f.is_default]
        conditional = [f for f in outgoing if f.condition is not None]
        if len(outgoing) < 2:
            warning("branch.single_outgoing", "分支节点出边少于 2 条，仅作透传", node=node.code)
        if len(defaults) > 1:
            error("branch.default.duplicate", "分支节点最多一条默认边", node=node.code)
        for flow in outgoing:
            if flow.is_default and flow.condition is not None:
                error(
                    "branch.default.has_condition",
                    "默认边不允许再挂条件",
                    node=node.code,
                    flow=flow.flow_code(),
                )
        if route_mode == RouteMode.FIRST_MATCH:
            if len(outgoing) > 1 and not defaults and len(conditional) < len(outgoing):
                error(
                    "branch.first_match.no_default",
                    "存在无条件出边且未设置默认边（条件全不满足时无法路由）",
                    node=node.code,
                )
            if outgoing and not defaults and conditional and len(conditional) == len(outgoing):
                warning(
                    "branch.first_match.all_conditional",
                    "出边全部带条件且无默认边：条件不互补时运行期将无法路由",
                    node=node.code,
                )
        else:  # ALL_MATCH：命中的都走
            for flow in outgoing:
                if flow.condition is None and not flow.is_default:
                    warning(
                        "branch.all_match.unconditional",
                        "无条件出边恒为真（等价并行分支，可考虑直接多条无条件出边）",
                        node=node.code,
                        flow=flow.flow_code(),
                    )

    # —— 开始条件（advance_policy）规则 ——
    for node in definition.nodes:
        if len(definition.incoming(node.code)) < 2:
            continue
        policy = getattr(node, "advance_policy", None)
        if policy is None:
            warning(
                "advance.missing",
                "节点有多条入边但未设置开始条件（缺省按「任一到达即开始」执行）",
                node=node.code,
            )
        # 「all 挂条件分支臂」不再告警：v2 §2.3 可达性放行下未命中臂不阻塞
        # （包容网关映射 = branch + all 汇聚，见 spec §2.4）

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
            source_is_branch = source_node is not None and source_node.kind == KIND_BRANCH
            if source_is_branch:
                warning(
                    "condition.missing_on_branch",
                    "分支节点出边未挂条件（仅默认边允许无条件）",
                    flow=flow.flow_code(),
                )

    # —— 变量引用（声明了流程变量时启用：条件/公式引用未知变量 → 预警）——
    declared = {v.name for v in definition.variables}
    if declared:
        for flow in flows:
            condition = flow.condition
            if condition is None:
                continue
            refs: set[str] = set()
            for group in condition.groups:
                for item in group:
                    root = item.left.split(".", 1)[0]
                    if root and root not in declared:
                        refs.add(root)
            if condition.formula:
                for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", condition.formula):
                    if name not in declared and name not in _FORMULA_NON_VARS:
                        refs.add(name)
            if refs:
                warning(
                    "condition.variable.unknown",
                    "条件引用了未声明的流程变量："
                    f"{', '.join(sorted(refs))}（可在「流程设置」中声明）",
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
