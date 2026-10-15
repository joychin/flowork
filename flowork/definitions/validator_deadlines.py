"""超时动作校验（validator 编排件的规则组五）：结构规则 + CEL 目标位遍历。

超时动作的全部校验知识住一处：动作列表上限、目标必填（reassign_to 与
escalate_to 均必填）、两目标的表达式位点产出、旧形态单条 timeout 的
strategy 取值（不在 notify/auto_approve/auto_reject 内＝运行期静默失效，
设计期 WARNING 提示改用 deadlines 列表）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition

__all__ = ["TARGET_FIELDS", "check_deadlines", "deadline_positions"]

IssueFn = Callable[..., None]

# 超时动作的 CEL 目标字段（结构校验与位点遍历共用一份知识）：两个动作的目标
# 都由图纸给出——取人语义归宿主（分工铁律）；科层组织写法
# 'manager_of(task.assignee)'。
TARGET_FIELDS = ("reassign_to", "escalate_to")

# 动作 → 必填目标字段（缺＝设计期 ERROR，不再拖到 fire 时才出故障单）
_REQUIRED_TARGET = {"reassign": "reassign_to", "escalate": "escalate_to"}

_MAX_DEADLINES = 8

# 旧形态单条 timeout 实际生效的策略（effective_deadlines 白名单同源）：
# 其余取值装载得过但超时静默失效
_EFFECTIVE_LEGACY_STRATEGIES = ("notify", "auto_approve", "auto_reject")


def _normalize(raw: Any) -> Any:
    """dict 袋（builder 无 registry 形态）→ TaskDeadline；其余原样。"""
    from flowork.definitions.node import TaskDeadline

    return TaskDeadline(**raw) if isinstance(raw, dict) else raw


def deadline_positions(node: Any) -> Iterator[tuple[str, str]]:
    """某节点的全部超时表达式位：yield (where, expression)。

    dict 袋静默 getattr None＝零校验——先归一。
    """
    for index, raw in enumerate(getattr(node, "deadlines", None) or []):
        deadline = _normalize(raw)
        for field in TARGET_FIELDS:
            expression = getattr(deadline, field, None)
            if expression:
                yield f"{node.code}.deadlines[{index}].{field}", expression


def check_deadlines(definition: ProcessDefinition, error: IssueFn, warning: IssueFn) -> None:
    """超时动作列表（escalate_to 必填；旧形态 timeout 失效策略 WARNING）。"""
    for node in definition.nodes:
        deadlines = list(getattr(node, "deadlines", None) or [])
        if len(deadlines) > _MAX_DEADLINES:
            error(
                "user_task.deadlines.too_many",
                f"超时动作列表上限 {_MAX_DEADLINES} 条（当前 {len(deadlines)}）",
                node=node.code,
            )
        # 旧形态 timeout 仅 user_task 携带（wait 的 timeout 是 WaitTimeout，
        # 无 strategy 字段）；builder 袋形态是裸 dict，与 deadlines 同惯例兼容
        if getattr(node, "kind", None) == "user_task":
            legacy = getattr(node, "timeout", None)
            if legacy is not None:
                strategy = (
                    legacy.get("strategy", "notify")
                    if isinstance(legacy, dict)
                    else getattr(legacy, "strategy", "notify")
                )
                if str(strategy) not in _EFFECTIVE_LEGACY_STRATEGIES:
                    warning(
                        "user_task.timeout.strategy_ineffective",
                        f"旧形态 timeout 的 strategy={strategy!r} 不生效"
                        "（仅 notify/auto_approve/auto_reject 三种；"
                        "全部五种动作请改用 deadlines 列表）",
                        node=node.code,
                    )
        for raw in deadlines:
            deadline = _normalize(raw)
            field = _REQUIRED_TARGET.get(str(deadline.action))
            if field and not getattr(deadline, field, None):
                # 引擎不含组织模型：升级目标必须由图纸给出——
                # 缺失＝设计期错误，而非等超时触发才出故障单。
                hint = "，如 manager_of(task.assignee)" if field == "escalate_to" else ""
                error(
                    f"user_task.deadline.{field}_missing",
                    f"{deadline.action} 动作必须带目标（{field}，CEL 表达式{hint}）"
                    "——引擎不含组织模型",
                    node=node.code,
                )
