"""参与者解析管线（M4 完整版）。

管线：表达式求值 → 手动选人覆盖 → 委托替换 → 去重策略（自动通过）
→ 无参与者兜底。产出 ``ParticipantResolution(todo, skipped)``：
skipped 为策略命中者（记 auto_pass 历史并计入同意数）。

未配置组织目录时回退到 participants.simple 的简化规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from flowork.engine.kernel import NoParticipantError
from flowork.participants.functions import builtin_functions, parse_expression
from flowork.participants.simple import resolve_simple

if TYPE_CHECKING:
    from flowork.definitions.builtin import UserTaskSpec
    from flowork.engine.context import RuntimeNodeContext
    from flowork.participants.functions import ParticipantFunctions
    from flowork.participants.org_directory import OrgDirectory

__all__ = ["ParticipantResolution", "resolve_participants"]


@dataclass
class ParticipantResolution:
    """解析结果：todo 待办人；skipped 策略命中（自动通过）人。"""

    todo: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def resolve_participants(
    ctx: RuntimeNodeContext,
    spec: UserTaskSpec,
    org: OrgDirectory | None,
    functions: ParticipantFunctions | None = None,
) -> ParticipantResolution:
    """完整解析管线。"""
    if org is None:
        return ParticipantResolution(todo=resolve_simple(ctx, spec))

    manual = (ctx.token.manual_participants or {}).get(spec.code) if ctx.token else None
    if manual:
        return ParticipantResolution(todo=_dedupe(manual))

    pool = _evaluate_expressions(ctx, spec, org, functions or builtin_functions())
    todo, skipped = _apply_strategies(ctx, spec, pool)
    return ParticipantResolution(todo=_dedupe(todo), skipped=_dedupe(skipped))


def _evaluate_expressions(
    ctx: RuntimeNodeContext,
    spec: UserTaskSpec,
    org: OrgDirectory,
    functions: ParticipantFunctions,
) -> list[str]:
    pool: list[str] = []
    expressions = ([spec.assignee] if spec.assignee else []) + list(spec.assignees)
    for expression in expressions:
        call = parse_expression(expression)
        if call is None:
            if expression == "originator":
                pool.append(ctx.instance.start_user)
            else:
                pool.append(expression)
        else:
            pool.extend(functions.call(call.name, ctx, org, call.args))
    return _dedupe(pool)


def _apply_strategies(
    ctx: RuntimeNodeContext, spec: UserTaskSpec, pool: list[str]
) -> tuple[list[str], list[str]]:
    """按策略把池中人员分流为 skipped（自动通过）与 todo。"""
    originator = ctx.instance.start_user
    handled_anywhere = _handled_anywhere(ctx)
    source_handlers = _handlers_of(ctx, _source_activity(ctx)) if spec.auto_pass_previous else set()

    todo: list[str] = []
    skipped: list[str] = []
    for user in pool:
        is_originator = spec.auto_pass_originator and user == originator
        is_previous = spec.auto_pass_previous and user in source_handlers
        is_participated = spec.auto_pass_participated and user in handled_anywhere
        if is_originator or is_previous or is_participated:
            skipped.append(user)
        else:
            todo.append(user)
    return todo, skipped


def _delegation_replace(todo: list[str], org: OrgDirectory) -> tuple[list[str], dict[str, str]]:
    """委托替换：返回（替换后待办人, 委托人→受托人映射）。"""
    replaced: list[str] = []
    mapping: dict[str, str] = {}
    for user in todo:
        trustee = org.delegation_of(user)
        if trustee and trustee != user:
            mapping[user] = trustee
            replaced.append(trustee)
        else:
            replaced.append(user)
    return _dedupe(replaced), mapping


def resolve_with_delegation(
    ctx: RuntimeNodeContext,
    spec: UserTaskSpec,
    org: OrgDirectory,
    functions: ParticipantFunctions | None = None,
) -> tuple[ParticipantResolution, dict[str, str]]:
    """解析 + 委托替换（供行为层使用）。"""
    resolution = resolve_participants(ctx, spec, org, functions)
    todo, mapping = _delegation_replace(resolution.todo, org)
    return ParticipantResolution(todo=todo, skipped=resolution.skipped), mapping


def fallback_when_empty(
    ctx: RuntimeNodeContext, spec: UserTaskSpec, org: OrgDirectory
) -> ParticipantResolution:
    """无参与者兜底：to_admins / raise。"""
    if spec.no_participant_policy == "to_admins" and org.admins:
        return ParticipantResolution(todo=list(org.admins))
    raise NoParticipantError(f"审批节点 {spec.code}（{spec.name or spec.code}）没有可用参与者")


def _handlers_of(ctx: RuntimeNodeContext, activity_code: str) -> set[str]:
    from sqlalchemy import select

    from flowork.persistence.orm import TaskHistoryRow

    if not activity_code:
        return set()
    rows = ctx.session.scalars(
        select(TaskHistoryRow).where(
            TaskHistoryRow.instance_id == ctx.instance.id,
            TaskHistoryRow.activity_code == activity_code,
            TaskHistoryRow.outcome != "cancelled",
        )
    )
    return {row.assignee for row in rows}


def _handled_anywhere(ctx: RuntimeNodeContext) -> set[str]:
    from sqlalchemy import select

    from flowork.persistence.orm import TaskHistoryRow

    rows = ctx.session.scalars(
        select(TaskHistoryRow).where(
            TaskHistoryRow.instance_id == ctx.instance.id,
            TaskHistoryRow.outcome != "cancelled",
        )
    )
    return {row.assignee for row in rows}


def _source_activity(ctx: RuntimeNodeContext) -> str:
    return ctx.token.source_activity_code or "" if ctx.token else ""


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
