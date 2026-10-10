"""候选名单并集去重（suite 层补充，A 批配套）。

候选制的完整语义（引用保留不展开 / 认领时经 is_member 判归属 / 无目录提示）见
``tests/semantics/test_candidate_claim.py``（A 批，2026-10-09）——本文件只补一条
suite 层走查：**具体人与候选组混写时的并集去重**（同一人既被点名又在组里，
候选关联不得出现两行；否则认领列表与规模护栏都会重复计数）。

来源：2026-10-09 候选制回归（并行会话 A 批 + 本会话兜底修复的交叉验证）。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.errors import TaskNotAvailableError
from tests.suite.features import covers


@covers("participant.candidate_users", "participant.candidate_groups")
def test_candidate_union_dedupes_people_and_groups(suite_engine: ProcessEngine) -> None:
    """具体人 ∪ 候选组：并集去重后逐项落候选关联，命中判定两式共用。

    u_cash 既被点名又在 role:finance_approver 组里——两条来源必须收敛成一个人。
    """
    engine = suite_engine
    definition = (
        FlowBuilder("claim_union")
        .start()
        .user_task(
            "pick",
            "候选人任务",
            candidate_users=["'u_cash'", "'u_ceo'"],
            candidate_groups=["'role:finance_approver'"],
        )
        .end()
        .connect("start", "pick")
        .connect("pick", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("claim_union", start_user="u_dev_liu")
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert task.assignee == "", "候选任务不落名"

    # 三条来源的人都能认领：点名两人 + 组里另一位（u_chief_acc）
    for user in ("u_cash", "u_ceo", "u_chief_acc"):
        engine.task.claim(task.id, user)
        engine.task.unclaim(task.id, user)
    # 不在名单里的既非点名也不在组内 → 拒绝
    with pytest.raises(TaskNotAvailableError):
        engine.task.claim(task.id, "u_dev_wu")

    engine.task.claim(task.id, "u_chief_acc")
    engine.task.complete(task.id, actor="u_chief_acc")
    assert engine.runtime.get_instance(iid).state == "completed"
