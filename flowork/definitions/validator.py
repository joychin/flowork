"""设计时校验器（编排器）：结构 / 可达性 / 条件分支 / 开始条件 / 参与者 / kind 注册。

不抛异常：返回 ``list[ValidateIssue]``，由调用方（CLI/发布服务）决定渲染与拦截；
存在 ERROR 级问题时禁止发布（``assert_valid``）。
规则实现拆至 validator_rules.py（结构类）与 validator_conditions.py（条件/语义类），
入口函数与 issue 类型不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from flowork.definitions.validator_conditions import (
    check_condition_legality,
    check_node_semantics,
    check_variable_references,
)
from flowork.definitions.validator_rules import (
    check_advance_policy,
    check_branch_rules,
    check_deadlines,
    check_structure,
    check_variables_and_org,
)
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

    def error(rule: str, message: str, **where: str) -> None:
        issues.append(ValidateIssue(IssueLevel.ERROR, rule, message, **where))

    def warning(rule: str, message: str, **where: str) -> None:
        issues.append(ValidateIssue(IssueLevel.WARNING, rule, message, **where))

    check_variables_and_org(definition, error, warning, org_configured=org_configured)
    check_structure(definition, registry, error, warning)
    check_branch_rules(definition, error, warning)
    check_advance_policy(definition, warning)
    check_deadlines(definition, error)
    check_node_semantics(definition, error, warning)
    check_condition_legality(definition, error, warning)
    check_variable_references(definition, warning)

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
