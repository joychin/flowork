"""变量 I/O 批（kernel-v2 spec §14 / variable-io-api.md）：wf_variable_history 测试。

验收：全写方血缘——发起（birth，old=None）、任务提交 output、钩子原地写
都产生逐键改前/改后行；值相等跳过；引擎内部写不发 variables_updated 事件
（spec §10：仅外部更新命令发射）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.persistence.orm import EventOutboxRow, VariableHistoryRow


@pytest.fixture
def engine() -> ProcessEngine:
    engine = ProcessEngine()
    engine.register_handler("stamp", lambda ctx, params: ctx.variables.update({"stamped": True}))
    definition = (
        FlowBuilder("hist_flow")
        .start()
        .user_task("a", assignee="'u_a'")
        .service_task("s", action="stamp")
        .end()
        .connect("start", "a")
        .connect("a", "s")
        .connect("s", "end")
        .build()
    )
    engine.repository.deploy(definition)
    return engine


def history_rows(
    engine: ProcessEngine, instance_id: str
) -> list[tuple[str, object, object, str, str | None]]:
    with engine.session_factory() as session:
        rows = session.scalars(
            select(VariableHistoryRow)
            .where(VariableHistoryRow.instance_id == instance_id)
            .order_by(VariableHistoryRow.created_at, VariableHistoryRow.id)
        ).all()
    return [(r.name, r.old_value, r.new_value, r.source, r.acted_by) for r in rows]


def outbox_event_names(engine: ProcessEngine, instance_id: str) -> set[str]:
    with engine.session_factory() as session:
        rows = session.scalars(
            select(EventOutboxRow).where(EventOutboxRow.instance_id == instance_id)
        ).all()
    return {r.event_name for r in rows}


def test_birth_task_output_and_hook_all_recorded(engine: ProcessEngine):
    """发起 birth 行（old=None）→ 任务提交行（source=task_output，归因 actor）
    → 钩子原地写行（同命令、命令级归因）。"""
    instance_id = engine.runtime.start_process(
        "hist_flow", start_user="u_s", variables={"amount": 100}
    )
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a", variables={"amount": 999})

    records = history_rows(engine, instance_id)
    rows = {(name, src): (old, new, actor) for name, old, new, src, actor in records}
    # birth：发起变量 old=None（改前快照在 session.add 后显式播种为空）
    assert rows[("amount", "engine")] == (None, 100, None)
    # 任务提交：100 → 999，归因到办理人
    assert rows[("amount", "task_output")] == (100, 999, "u_a")
    # 钩子（service_task handler 原地写）：同一条命令内，命令级归因
    assert rows[("stamped", "task_output")] == (None, True, "u_a")


def test_unchanged_value_skipped(engine: ProcessEngine):
    """值相等不产生行——留痕表只记真实变化。"""
    instance_id = engine.runtime.start_process(
        "hist_flow", start_user="u_s", variables={"amount": 100, "memo": "keep"}
    )
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a", variables={"amount": 100})

    names = {name for name, *_ in history_rows(engine, instance_id)}
    assert "amount" in names  # 仅 birth 行（old=None→100）
    assert len([n for n in names if n == "amount"]) == 1
    assert "memo" in names  # birth 行
    # amount: 任务提交值未变（100→100），不再追加行
    amount_rows = [r for r in history_rows(engine, instance_id) if r[0] == "amount"]
    assert len(amount_rows) == 1


def test_internal_writes_emit_no_variables_updated_event(engine: ProcessEngine):
    """spec §10：引擎内部写变量（发起/任务提交/钩子）不发 variables_updated。"""
    instance_id = engine.runtime.start_process(
        "hist_flow", start_user="u_s", variables={"amount": 100}
    )
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a", variables={"amount": 999})

    assert "variables_updated" not in outbox_event_names(engine, instance_id)
    assert history_rows(engine, instance_id)  # 但留痕行存在（血缘走表不走事件）
