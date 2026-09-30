"""参与者解析（M1 简化版）。

规则：手动选人（token.manual_participants，M3 起写入）优先；
否则按 assignee + assignees 顺序解析，``originator`` 解析为发起人。
M4 由完整 ParticipantResolver（组织目录/取人函数/策略/委托）替换本实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.definitions.builtin import UserTaskSpec

if TYPE_CHECKING:
    from flowork.engine.context import RuntimeNodeContext

__all__ = ["ORIGINATOR", "resolve_simple"]

ORIGINATOR = "originator"


def resolve_simple(ctx: RuntimeNodeContext, spec: UserTaskSpec) -> list[str]:
    """解析审批节点参与者（保序去重）。"""
    if ctx.token is not None:
        manual = (ctx.token.manual_participants or {}).get(spec.code)
        if manual:
            return _dedupe(manual)
    originator = ctx.instance.start_user
    raw: list[str] = []
    if spec.assignee:
        raw.append(spec.assignee)
    raw.extend(spec.assignees)
    resolved = [originator if p == ORIGINATOR else p for p in raw]
    return _dedupe(resolved)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
