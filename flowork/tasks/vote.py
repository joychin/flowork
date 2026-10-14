"""表决行判定（spec §5.1）：名单类查询的统一口径。

任务历史行记录的是「任务行的终结」，不是「人的表态」——转办、协办、咨询、
会签出口自动通过都会落行，但都不代表一票。此前各消费方自行推导「谁办过」，
清一色 ``outcome != "cancelled"``，六处同盲（驳回复活名单吞行家族）。
本模块立单一谓词，统辖名单类查询：

- **表决行** ＝ 审批类任务（task_type=approval）+ 真实表态（outcome ∈
  agree/reject；超时代投经 complete 落 agree，acted_by 留归因）。消费方：
  驳回复活名单 / prev_task·node() 判断面 / auto_pass_participated /
  auto_pass_previous / 撤回下游进展判据①。
- **轮次消耗行** ＝ 审批类任务且已终结（含 transfer/auto_pass）。消费方：
  串行会签下一棒——被策略跳过与转手的人都算「轮次已消耗」，不重派；
  协办/咨询行不算（此前会把下一棒吞掉致无声死锁）。

两处语义裁定：auto_pass（机器代投）不算表决——复活不拉、
参与不算，一次代投不得滚成终身代投；转办人不算「已参与」——票由被转办人
代为行使（§5.1 transfer 条款），多问一次人优于替人投一票。
"""

from __future__ import annotations

from sqlalchemy import ColumnElement

from flowork.persistence.orm import TaskHistoryRow

__all__ = [
    "VOTED_OUTCOMES",
    "consumed_turn_criteria",
    "vote_criteria",
]

VOTED_OUTCOMES = frozenset({"agree", "reject"})


def vote_criteria() -> list[ColumnElement[bool]]:
    """表决行的 SQL 下推件：``.where(…, *vote_criteria())``。"""
    return [
        TaskHistoryRow.task_type == "approval",
        TaskHistoryRow.outcome.in_(VOTED_OUTCOMES),
    ]


def consumed_turn_criteria() -> list[ColumnElement[bool]]:
    """轮次消耗行的 SQL 下推件（串行会签下一棒专用）。

    审批类且已终结：transfer（轮次被转手消耗）/auto_pass（被策略跳过消耗）/
    agree/reject（真实表决）都算；协办/咨询（task_type 过滤）与取消不算。
    """
    return [
        TaskHistoryRow.task_type == "approval",
        TaskHistoryRow.outcome != "cancelled",
    ]
