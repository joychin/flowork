"""任务动作表（v2.3 第二批 A1）：按 (state × task_type × 认领态) 声明可用动作。

形态对齐 jBPM 的 (操作 × 状态) 声明表——**不是**节点级配置（行业对拍：BPMN 无
此位置，主流引擎不做节点级动作白名单；对标产品 的 permittedAction 是孤例）。
分组借 Camunda 8 的动词切分：work＝出结果（改变令牌推进），dispatch＝改派类
（转移办理权）。本表是**引擎自己的事实面**：谁能做属宿主权限（M-A/插件），
表只回答「这个任务当前允许哪些动作」。

消费点：① ``TaskInfo.available_actions`` 服务端算完下发（主流引擎均无此面——
AI/前端拿到任务即知能做什么，三面同构受益）；② 动作入口守卫
``assert_action_allowed``（不可用 → TaskNotAvailableError）。
withdraw 不在本表：它作用于**已办历史行**而非活表任务（入口见 TaskReworkMixin，
带独立下游闸门）。
"""

from __future__ import annotations

from flowork.errors import TaskNotAvailableError
from flowork.persistence.orm import TaskRow

__all__ = ["ACTION_GROUPS", "allowed_actions", "assert_action_allowed"]

# 动词分组（Camunda 8 切分）：work＝办（出结果），dispatch＝分派（转移办理权）
ACTION_GROUPS: dict[str, frozenset[str]] = {
    "work": frozenset({"complete", "reject"}),
    "dispatch": frozenset({"transfer", "assist", "add_countersigner", "claim", "unclaim"}),
}

_ASSIGNED_ACTIONS = [
    "complete",
    "reject",
    "transfer",
    "assist",
    "add_countersigner",
    "consult",
    "unclaim",
]


def allowed_actions(task: TaskRow) -> list[str]:
    """活表任务当前可做的动作（服务端算完的「状态×类型×认领态」三判定）。"""
    if task.state != "ready":
        return []
    if task.task_type == "assist":
        return ["complete_assist"]  # 协办只交还意见，不参与推进（spec §5.1）
    if task.task_type == "consult":
        return ["complete_consult"]  # 咨询意见任务：只交回意见（v2.3 A8）
    if task.assignee == "":
        return ["claim"]  # 候选任务：先认领（complete 须 assignee 已固定）
    return list(_ASSIGNED_ACTIONS)


def assert_action_allowed(action: str, task: TaskRow) -> None:
    """动作表守卫：动作不在该任务可用集 → 领域拒绝（统一消息）。"""
    if action not in allowed_actions(task):
        raise TaskNotAvailableError(
            f"任务不可{action}（{task.state}/{task.task_type}"
            f"{'/未认领' if task.assignee == '' else ''} 不适用）：{task.id}"
        )
