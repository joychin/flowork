"""套件·会签×驳回×时限耦合世界（flowork 特色语义，业界无可对标）。

一个节点同时承载三族语义的交叉：
- 比例会签出口（approve_exit 60%，整数乘法精确等价 ceil）
- 否决缺省＝全员（disapprove_exit 缺省：一次驳回只是一张否决票）
- 三档任务时限（notify@5 / reassign@10→u_lead_be / auto_approve@15），
  时限按任务项绑定——会签每一项各自持有 Job 组，改派与自动通过逐项生效
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable
from flowork.definitions.node import MultiInstance, TaskDeadline

KNOT_MEMBERS = ["'u_chief_acc'", "'u_cash'", "'u_cfo'"]  # 三人会签
RATIO_EXIT = "task.approve_count * 100 >= task.item_count * 60"


def countersign_knot_definition() -> ProcessDefinition:
    return (
        FlowBuilder("suite_countersign_knot", "套件·会签耦合")
        .start()
        .user_task(
            "quick",
            "主管快审",
            assignee="manager_of(initiator)",
            reject_target="start",
        )
        .user_task(
            "knot",
            "财务会签",
            assignees=KNOT_MEMBERS,
            multi_instance=MultiInstance(mode="parallel", approve_exit=RATIO_EXIT),
            reject_target="start",
            deadlines=[
                TaskDeadline(after_minutes=5, action="notify"),
                TaskDeadline(after_minutes=10, action="reassign", reassign_to="'u_lead_be'"),
                TaskDeadline(after_minutes=15, action="auto_approve"),
            ],
        )
        .end("knot_end", "耦合结束")
        .flow("start", "quick")
        .flow("quick", "knot")
        .flow("knot", "knot_end")
        .build()
        .model_copy(
            update={
                "variables": [ProcessVariable(name="note", type="string", label="备注", sample="")]
            }
        )
    )
