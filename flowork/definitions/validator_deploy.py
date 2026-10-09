"""部署期引用校验（v2.3 第三批 A11，L3+L4——spec §6/设计稿 deploy-validation-design.md）。

与纯设计时校验（validator*.py，无 I/O）分居：本模块**要查库/查注册表/查目录**，
只由 ``RepositoryService.deploy`` 在事务内调用（设计时 validate() 不跑这两层）。

分层与级别（行业依据 delta-industry-benchmark §11：组织实体引用部署期全行业
不校验——flowork 自持目录（M-A）有能力做，但默认只 WARNING，strict 可升 ERROR）：

- **L3 定义内引用**：
  - ``call_activity`` 目标定义——``binding="deployment"``：目标未发布 → **ERROR**
    （Zeebe deployment binding 同义：部署时必须存在）；``binding="latest"``（缺省）：
    未发布 → WARNING（子流程可独立部署，运行期解析最新版——自引用跳过）。
  - ``service_task`` 的 handler 未注册 → WARNING（**合法模式**：先 deploy 后
    register_handler，tests/semantics/test_incident_resolution 即此；strict 升 ERROR）。
- **L4 目录实体存在性**：取人位（assignee/assignees/recipients/reassign_to）CEL
  表达式里的**字符串字面量**逐个问 ``subject_exists`` 钩子：False → WARNING
  （strict → ERROR）；None → 静默跳过（目录未实现该能力，零噪音）。

纪律（spec 第十轮）：**可见优于静默**——默认 WARNING 不拦发布但留痕（logger），
``EngineConfig.strict_deploy_validation=True`` 全部升 ERROR 拦发布。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from flowork.definitions.builtin import KIND_CALL_ACTIVITY, KIND_CC_TASK, KIND_SERVICE_TASK
from flowork.definitions.builtin import KIND_USER_TASK as _KIND_USER_TASK
from flowork.definitions.validator import IssueLevel, ValidateIssue
from flowork.expr.cel import (
    FormulaError,
    _parse,  # cel.py 行数红线，字面量提取住本模块
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.definition import ProcessDefinition

logger = logging.getLogger(__name__)

__all__ = ["check_deploy_references", "string_literals"]

SubjectExists = Callable[[str], bool | None]


def string_literals(formula: str) -> list[str]:
    """取 CEL 表达式里的全部字符串字面量（去引号；编译错返回空——语法层另有 L2 拦）。"""
    try:
        tree = _parse(formula)
    except FormulaError:
        return []
    found: list[str] = []

    def visit(node: Any) -> None:
        data = getattr(node, "data", None)
        if data is None:
            if getattr(node, "type", None) == "STRING_LIT":
                found.append(str(node)[1:-1])
            return
        for child in node.children:
            visit(child)

    visit(tree)
    return found


def check_deploy_references(
    definition: ProcessDefinition,
    *,
    session: Session,
    handlers: tuple[str, ...],
    subject_exists: SubjectExists | None = None,
    strict: bool = False,
) -> list[ValidateIssue]:
    """部署期 L3+L4：引用完整性与目录实体存在性（默认 WARNING，strict 升 ERROR）。"""
    issues: list[ValidateIssue] = []

    def issue(default: IssueLevel, rule: str, message: str, node: str) -> None:
        level = IssueLevel.ERROR if strict else default
        issues.append(ValidateIssue(level, rule, message, node=node))

    for node in definition.nodes:
        if node.kind == KIND_CALL_ACTIVITY:
            _check_call_target(definition, node, session, issue)
        elif node.kind == KIND_SERVICE_TASK:
            action = getattr(node, "action", "") or ""
            if action and action not in handlers:
                issue(
                    IssueLevel.WARNING,
                    "service_task.handler_unknown",
                    f"handler {action!r} 未注册（可后注册；运行期解析不到将出故障单）",
                    node.code,
                )
    if subject_exists is not None:
        _check_subject_literals(definition, subject_exists, issue)
    for entry in issues:  # 可见优于静默：WARNING 也留痕
        logger.warning("deploy 校验：%s", entry)
    return issues


def _check_call_target(
    definition: ProcessDefinition, node: Any, session: Session, issue: Any
) -> None:
    from sqlalchemy import select

    from flowork.persistence.orm import ProcessDefinitionRow

    target = getattr(node, "process_code", "") or ""
    if not target or target == definition.code:  # 自引用：运行期解析自身，合法
        return
    published = (
        session.scalars(
            select(ProcessDefinitionRow.id)
            .where(ProcessDefinitionRow.code == target, ProcessDefinitionRow.status == "published")
            .limit(1)
        ).first()
        is not None
    )
    if published:
        return
    binding = getattr(node, "binding", "latest")
    if binding == "deployment":
        issue(
            IssueLevel.ERROR,
            "call_activity.target_missing",
            f"子流程定义 {target!r} 未发布（binding=deployment：部署时必须存在）",
            node.code,
        )
    else:
        issue(
            IssueLevel.WARNING,
            "call_activity.target_unpublished",
            f"子流程定义 {target!r} 尚未发布（binding=latest：运行期解析最新版；"
            "独立部署合法，注意先发子后发父）",
            node.code,
        )


def _check_subject_literals(
    definition: ProcessDefinition,
    subject_exists: SubjectExists,
    issue: Any,
) -> None:
    """取人位字面量逐个问目录（L4）：False=WARNING/strict ERROR；None=静默跳过。"""
    positions: list[tuple[str, str]] = []
    for node in definition.nodes:
        if node.kind == _KIND_USER_TASK:
            assignee = getattr(node, "assignee", None)
            if assignee:
                positions.append((f"{node.code}.assignee", assignee))
            positions.extend(
                (f"{node.code}.assignees[{i}]", e)
                for i, e in enumerate(getattr(node, "assignees", None) or [])
            )
            positions.extend(
                (f"{node.code}.candidate_users[{i}]", e)
                for i, e in enumerate(getattr(node, "candidate_users", None) or [])
            )
            positions.extend(
                (f"{node.code}.deadlines[{j}].reassign_to", d.reassign_to)
                for j, d in enumerate(getattr(node, "deadlines", None) or [])
                if d.reassign_to
            )
            positions.extend(
                (f"{node.code}.deadlines[{j}].escalate_to", d.escalate_to)
                for j, d in enumerate(getattr(node, "deadlines", None) or [])
                if d.escalate_to
            )
        elif node.kind == KIND_CC_TASK:
            positions.extend(
                (f"{node.code}.recipients[{i}]", e)
                for i, e in enumerate(getattr(node, "recipients", None) or [])
            )
    seen: set[tuple[str, str]] = set()
    for where, expression in positions:
        for literal in string_literals(expression):
            if (where, literal) in seen:
                continue
            seen.add((where, literal))
            verdict = subject_exists(literal)
            if verdict is False:
                issue(
                    IssueLevel.WARNING,
                    "participant.subject_unknown",
                    f"取人位 {where} 的字面量 {literal!r} 在目录中不存在"
                    "（目录未实现 subject_exists 时本检查跳过）",
                    where.split(".")[0],
                )
