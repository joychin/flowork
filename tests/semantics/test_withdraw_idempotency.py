"""withdraw 重复撤回守卫（全仓 review 2026-10-13 批 3 P1-1）。

修前：withdraw 是唯一作用于历史行的动作入口，无幂等/占位守卫——对同一条
已办历史行连续两次 withdraw（用户双击/前端重试即触发）第二次仍成功，
单人节点出现双并行未完令牌 + 双同 assignee 待办；完成其一后另一令牌
永久 unfinished、幽灵待办无人收口（无故障单）。

守卫判据＝**本人在本节点已有在办待办则拒绝**（活表存在即在办，归档即删行）。
不按节点全拦：并行会签的合法撤票场景里，其他会签人的在办任务同节点存在，
只有「撤回者的待办已重建未办」才是重复撤回。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.builtin import MultiInstance
from flowork.engine.engine import ProcessEngine
from flowork.errors import TaskNotAvailableError
from flowork.persistence.orm import TaskRow


def _serial_engine() -> ProcessEngine:
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("wd_serial")
        .start()
        .user_task("a", assignee="'u1'")
        .user_task("b", assignee="'u2'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    return engine


def _countersign_engine() -> ProcessEngine:
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("wd_cs")
        .start()
        .user_task(
            "cs",
            assignees=["'v1'", "'v2'"],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "cs")
        .connect("cs", "end")
        .build()
    )
    return engine


def _live_tasks(engine: ProcessEngine, instance_id: str, code: str) -> list[TaskRow]:
    def _cmd(ctx):
        from sqlalchemy import select

        return list(
            ctx.session.scalars(
                select(TaskRow).where(
                    TaskRow.instance_id == instance_id, TaskRow.activity_code == code
                )
            )
        )

    result: list[TaskRow] = engine.execute(_cmd)
    return result


def test_second_withdraw_of_same_history_refused():
    """双撤回：第二次拒绝，节点恰一个在办令牌一条待办（修前产幽灵双份）。"""
    engine = _serial_engine()
    instance_id = engine.runtime.start_process("wd_serial", start_user="u1")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task_a.id, actor="u1")  # a 办结，b 激活
    done_a = next(t for t in engine.history.task_history(instance_id) if t.activity_code == "a")

    engine.task.withdraw(done_a.task_id, actor="u1")  # 第一次撤回：合法
    live = _live_tasks(engine, instance_id, "a")
    assert len(live) == 1

    with pytest.raises(TaskNotAvailableError, match="重复撤回"):
        engine.task.withdraw(done_a.task_id, actor="u1")  # 双击/重试
    assert len(_live_tasks(engine, instance_id, "a")) == 1

    # 守卫后流程仍可正常走完（被拦的只是重复动作）
    engine.task.complete(_live_tasks(engine, instance_id, "a")[0].id, actor="u1")
    engine.task.complete(_live_tasks(engine, instance_id, "b")[0].id, actor="u2")
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_parallel_countersign_withdraw_not_blocked_by_peers():
    """对照组：并行会签他人任务在办不拦本人撤票（守卫只认本人在办）。"""
    engine = _countersign_engine()
    instance_id = engine.runtime.start_process("wd_cs", start_user="u1")
    tasks = {t.assignee: t for t in engine.task.list_tasks(instance_id=instance_id)}
    engine.task.complete(tasks["v1"].id, actor="v1")  # v1 先投一票

    done_v1 = next(
        t
        for t in engine.history.task_history(instance_id)
        if t.activity_code == "cs" and t.assignee == "v1"
    )
    engine.task.withdraw(done_v1.task_id, actor="v1")  # 合法撤票（v2 仍在办）

    live = _live_tasks(engine, instance_id, "cs")
    assert sorted(t.assignee for t in live) == ["v1", "v2"]  # v1 撤票重建 + v2 原任务
    # 撤票后重投 + v2 投票 → 出口达成完成
    rebuilt_v1 = next(t for t in live if t.assignee == "v1")
    engine.task.complete(rebuilt_v1.id, actor="v1")
    engine.task.complete(_live_tasks(engine, instance_id, "cs")[0].id, actor="v2")
    assert engine.runtime.get_instance(instance_id).state == "completed"
