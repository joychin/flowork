"""套件时限/消息/生命周期流程定义 + 可控时钟。

- suite_deadline_notify：节点级多档 deadlines（notify/reassign/escalate/auto_approve）；
- suite_deadline_reject：auto_reject 驳回到 start；
- suite_deadline_{mark,cancel,terminate}：实例级时限三策略；
- suite_message：消息关联（订阅/缓冲/信号）；
- suite_lifecycle：幂等启动 + 暂停恢复（timer 冻结）；
- suite_fragile：service 故障 → Incident → resolve 五动作。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import InstanceDeadline, ProcessDefinition
from flowork.definitions.node import TaskDeadline


class FakeClock:
    """可控时钟：EngineConfig(now=...) 注入，tick 前推进分钟数。"""

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 8, 9, 0, 0)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, minutes: int) -> None:
        self.value += timedelta(minutes=minutes)


def deadline_notify_definition() -> ProcessDefinition:
    """预警 + 升级两档（escalate 会移交任务、归档原任务——后续档随之跳过）。"""
    return (
        FlowBuilder("suite_deadline_notify", "套件·预警升级")
        .start()
        .user_task(
            "hold",
            "限时审批",
            assignee="'u_dev_liu'",
            reject_target="start",
            deadlines=[
                TaskDeadline(after_minutes=5, action="notify"),
                TaskDeadline(after_minutes=10, action="escalate"),
            ],
        )
        .end("notify_end", "超时流程结束")
        .flow("start", "hold")
        .flow("hold", "notify_end")
        .build()
    )


def deadline_reassign_definition() -> ProcessDefinition:
    """改派 + 其后一档 auto_approve：移交归档原任务后，auto 档按已终结跳过。"""
    return (
        FlowBuilder("suite_deadline_reassign", "套件·超时改派")
        .start()
        .user_task(
            "gate",
            "改派闸口",
            assignee="'u_dev_liu'",
            deadlines=[
                TaskDeadline(after_minutes=5, action="reassign", reassign_to="u_lead_be"),
                TaskDeadline(after_minutes=10, action="auto_approve"),
            ],
        )
        .end("reassign_end", "改派流程结束")
        .flow("start", "gate")
        .flow("gate", "reassign_end")
        .build()
    )


def deadline_auto_definition() -> ProcessDefinition:
    """预警 + 自动通过两档：原任务未被移交，两档先后生效。"""
    return (
        FlowBuilder("suite_deadline_auto", "套件·超时自动通过")
        .start()
        .user_task(
            "gate",
            "自动闸口",
            assignee="'u_dev_liu'",
            deadlines=[
                TaskDeadline(after_minutes=5, action="notify"),
                TaskDeadline(after_minutes=10, action="auto_approve"),
            ],
        )
        .end("auto_end", "自动通过流程结束")
        .flow("start", "gate")
        .flow("gate", "auto_end")
        .build()
    )


def deadline_reject_definition() -> ProcessDefinition:
    return (
        FlowBuilder("suite_deadline_reject", "套件·超时自动驳回")
        .start()
        .user_task(
            "gate",
            "限时闸口",
            assignee="'u_dev_liu'",
            reject_target="start",
            deadlines=[TaskDeadline(after_minutes=10, action="auto_reject")],
        )
        .end("reject_end", "闸口流程结束")
        .flow("start", "gate")
        .flow("gate", "reject_end")
        .build()
    )


def instance_deadline_definition(strategy: str) -> ProcessDefinition:
    """实例级时限（strategy: mark/cancel/terminate），code = suite_deadline_{strategy}。"""
    return (
        FlowBuilder(f"suite_deadline_{strategy}", f"套件·实例时限·{strategy}")
        .start()
        .user_task("hold", "滞留节点", assignee="'u_dev_wu'")
        .end("instance_end", "实例时限流程结束")
        .flow("start", "hold")
        .flow("hold", "instance_end")
        .build()
        .model_copy(update={"deadline": InstanceDeadline(after_minutes=60, strategy=strategy)})
    )


MESSAGE_INPUT = {
    "type": "object",
    "properties": {
        "amount": {"type": "number", "minimum": 0},
        "paid": {"type": "boolean"},
    },
    "required": ["amount"],
}


def message_definition() -> ProcessDefinition:
    return (
        FlowBuilder("suite_message", "套件·消息关联")
        .start()
        .user_task("await_paid", "等待回款", assignee="'u_buyer'")
        .service_task("echo", "回款回执", action="echo_paid")
        .end("message_end", "消息流程结束")
        .flow("start", "await_paid")
        .flow("await_paid", "echo")
        .flow("echo", "message_end")
        .build()
        .model_copy(update={"input": MESSAGE_INPUT})
    )


def lifecycle_definition() -> ProcessDefinition:
    return (
        FlowBuilder("suite_lifecycle", "套件·实例生命周期")
        .start()
        .user_task(
            "first_task",
            "第一环",
            assignee="'u_dev_wu'",
            deadlines=[TaskDeadline(after_minutes=30, action="notify")],
        )
        .user_task("second_task", "第二环", assignee="'u_cto'")
        .end("lifecycle_end", "生命周期流程结束")
        .flow("start", "first_task")
        .flow("first_task", "second_task")
        .flow("second_task", "lifecycle_end")
        .build()
    )


def fragile_definition() -> ProcessDefinition:
    return (
        FlowBuilder("suite_fragile", "套件·脆弱链路")
        .start()
        .user_task("submit", "提交", assignee="'u_dev_wu'")
        .service_task("boom", "外部调用", action="explode")
        .user_task("after", "善后", assignee="'u_cto'")
        .end("fragile_end", "脆弱链路结束")
        .flow("start", "submit")
        .flow("submit", "boom")
        .flow("boom", "after")
        .flow("after", "fragile_end")
        .build()
    )
