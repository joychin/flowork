"""表达式求值上下文构造（kernel-v2 §4.3/C.4）：全表达式位共用的唯一入口。

变量命名空间（spec §4.3）：``input.*``（契约入参，存法由调用方约定）、
``vars``（实例变量，现状拍平存）、``initiator``（发起人 Subject 保留标识符）、
``task``（任务快照，超时求值等位点可用，spec C.4 固定形状）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["expression_context"]


def expression_context(
    variables: Mapping[str, Any],
    *,
    start_user: str | None = None,
    task: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造求值上下文副本：变量 + ``initiator`` + 可选 ``task`` 快照。

    显式同名变量优先（setdefault）：声明了 ``initiator``/``task`` 变量时不覆盖。
    task 快照做防御性副本——spec C.4 固定形状（task.id / task.assignee /
    task.outcome），活任务（超时触发时）outcome 为 None。
    """
    payload = dict(variables)
    if start_user:
        payload.setdefault("initiator", start_user)
    if task is not None:
        payload.setdefault("task", dict(task))
    return payload
