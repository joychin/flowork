"""参与者解析管线（单轨 CEL，kernel-v2 §4.2 位点统一）。

管线：手动选人覆盖 → CEL 公式求值 → 委托替换 → 去重策略（自动通过）
→ 无参与者兜底。产出 ``ParticipantResolution(todo, skipped)``：
skipped 为策略命中者（记 auto_pass 历史并计入同意数）。

assignee/assignees 每项为 **CEL 表达式**：字面人写字符串字面量 ``'u_a'``，
函数/变量引用如 ``manager_of(initiator)``、``users_with_role("finance")``。
查无传播（spec §5.5）：表达式求值为 null/空列表时贡献空，不报错；
求值错（FormulaError）按错误二分包 EngineExecutionError（挂起+故障单，§8）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from flowork.engine.kernel import EngineExecutionError, NoParticipantError, ParticipantLimitError
from flowork.expr.cel import FormulaError, evaluate_formula_value, references_identifier
from flowork.expr.context import expression_context

if TYPE_CHECKING:
    from flowork.definitions.builtin import UserTaskSpec
    from flowork.engine.context import RuntimeNodeContext
    from flowork.participants.org_directory import OrgDirectoryLike

__all__ = [
    "ParticipantResolution",
    "assert_participant_limit",
    "resolve_participants",
    "resolve_subjects",
    "resolve_with_delegation",
]

Functions = Mapping[str, Callable[..., object]]


@dataclass
class ParticipantResolution:
    """解析结果：todo 待办人；skipped 策略命中（自动通过）人。"""

    todo: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def assert_participant_limit(count: int, limit: int | None, *, where: str) -> None:
    """参与者规模护栏：超限拒绝（技术故障），绝不静默截断（spec v2.3 第八轮）。"""
    if limit is not None and count > limit:
        raise ParticipantLimitError(
            f"{where}解析出 {count} 名参与者，超过上限 {limit}（EngineConfig.max_participants）"
            "——拒绝而非截断；请修正定义/接收人名单，或显式调大配置"
        )


def resolve_subjects(
    expressions: list[str],
    variables: Mapping[str, Any],
    start_user: str | None,
    functions: Functions | None = None,
    *,
    label: str = "",
    what: str = "取人",
    instance: Mapping[str, Any] | None = None,
) -> list[str]:
    """对一组取人表达式（CEL）求值 → 具体人列表（保序去重）。

    供解析管线与加签/传阅等场景共用——重排场景必须存具体人，不能存表达式原文。
    语义同取人位：null/空＝查无传播；str/list[str]＝单人/多人；其他类型报错。
    label 为错误消息里的位置标注（如节点码）；what 为错误措辞（取人/传阅接收人）。
    ``instance`` 全局事实（id/started_at/business_key）可选注入，调用方持实例行时传。
    """
    where = f"{label}：" if label else ""
    pool: list[str] = []
    context = expression_context(variables, start_user=start_user, instance=instance)
    for expression in expressions:
        if "prev_task" not in context and references_identifier(expression, "prev_task"):
            continue  # 上一站查无：贡献空（expression-dataface §3，对齐 §5.5）
        try:
            result = evaluate_formula_value(expression, context, functions=functions)
        except FormulaError as exc:
            raise EngineExecutionError(
                f"{what}公式求值失败（{where}{expression!r}）：{exc}"
            ) from exc
        if result is None:
            continue  # 查无传播（spec §5.5）
        if isinstance(result, str):
            pool.append(str(result))
        elif isinstance(result, list):
            pool.extend(str(user) for user in result)
        else:
            raise NoParticipantError(
                f"{what}公式须返回人或人列表（{where}{expression!r} → {type(result).__name__}，"
                "谓词函数属条件位）"
            )
    return _dedupe(pool)


def resolve_participants(
    ctx: RuntimeNodeContext,
    spec: UserTaskSpec,
    functions: Functions | None = None,
) -> ParticipantResolution:
    """完整解析管线（单轨 CEL；委托与兜底由 resolve_with_delegation/调用方叠加）。"""
    manual = (ctx.token.manual_participants or {}).get(spec.code) if ctx.token else None
    if manual:
        return ParticipantResolution(todo=_dedupe(manual))

    pool = _evaluate_expressions(ctx, spec, functions)
    todo, skipped = _apply_strategies(ctx, spec, pool)
    return ParticipantResolution(todo=_dedupe(todo), skipped=_dedupe(skipped))


def _evaluate_expressions(
    ctx: RuntimeNodeContext,
    spec: UserTaskSpec,
    functions: Functions | None,
) -> list[str]:
    """解析管线内用：assignee/assignees 表达式 → 人池（错误消息带节点码）。

    取人位判断面接线（expression-dataface §2.2/§2.3）：变量带 prev_task 快照、
    函数表并入 node()——InMemoryNodeContext（无 session）裸跑不受影响。
    """
    expressions = ([spec.assignee] if spec.assignee else []) + list(spec.assignees)
    from flowork.tasks.facts import attach_node, instance_facts, with_prev_task

    session = getattr(ctx, "session", None)
    return resolve_subjects(
        expressions,
        with_prev_task(session, ctx.instance.id, ctx.variables),
        ctx.instance.start_user,
        attach_node(functions, session, ctx.instance.id),
        label=spec.code,
        instance=instance_facts(ctx.instance),
    )


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


def _delegation_replace(todo: list[str], org: OrgDirectoryLike) -> tuple[list[str], dict[str, str]]:
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
    org: OrgDirectoryLike,
    functions: Functions | None = None,
) -> tuple[ParticipantResolution, dict[str, str]]:
    """解析 + 委托替换（供行为层使用）。"""
    resolution = resolve_participants(ctx, spec, functions)
    todo, mapping = _delegation_replace(resolution.todo, org)
    return ParticipantResolution(todo=todo, skipped=resolution.skipped), mapping


def fallback_when_empty(
    ctx: RuntimeNodeContext, spec: UserTaskSpec, org: OrgDirectoryLike
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
