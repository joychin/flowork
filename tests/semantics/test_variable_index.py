"""P1-5（kernel-v2 spec §14/§16）：wf_variable_index 测试。

验收：索引与变量同事务一致——命令边界重写（发起/任务提交均覆盖），
非 searchable 不入索引；数值/文本分槽；检索按索引命中。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.engine.engine import ProcessEngine
from flowork.persistence.orm import VariableIndexRow
from flowork.persistence.variable_index import instances_by_variable


@pytest.fixture
def engine() -> ProcessEngine:
    engine = ProcessEngine()
    definition = (
        FlowBuilder("idx_flow")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    definition.variables = [
        ProcessVariable(name="order_no", type="string", sample="X-1", searchable=True),
        ProcessVariable(name="amount", type="number", sample=1, searchable=True),
        ProcessVariable(name="urgent", type="boolean", sample=True, searchable=True),
        ProcessVariable(name="memo", type="string", sample=""),  # 非 searchable
    ]
    engine.repository.deploy(definition)
    return engine


def index_rows(
    engine: ProcessEngine, instance_id: str
) -> dict[str, tuple[str | None, float | None]]:
    with engine.session_factory() as session:
        rows = session.scalars(
            select(VariableIndexRow).where(VariableIndexRow.instance_id == instance_id)
        ).all()
    return {r.name: (r.value_text, r.value_num) for r in rows}


def test_index_synced_on_start(engine: ProcessEngine):
    instance_id = engine.runtime.start_process(
        "idx_flow",
        start_user="u_s",
        variables={"order_no": "X-9", "amount": 500, "urgent": True, "memo": "不索引"},
    )
    rows = index_rows(engine, instance_id)
    assert rows["order_no"] == ("X-9", None)  # 文本槽
    assert rows["amount"] == (None, 500.0)  # 数值槽
    assert rows["urgent"] == ("true", None)  # 布尔入文本槽
    assert "memo" not in rows  # 非 searchable 不入索引


def test_index_updated_on_task_output_same_transaction(engine: ProcessEngine):
    """验收：任务提交改写变量——索引行同命令重写（无旧值残留）。"""
    instance_id = engine.runtime.start_process(
        "idx_flow", start_user="u_s", variables={"order_no": "X-1", "amount": 100}
    )
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a", variables={"amount": 999})
    rows = index_rows(engine, instance_id)
    assert rows["amount"] == (None, 999.0)
    assert len([r for r in rows if r == "amount"]) == 1  # 旧值被替换非追加


def test_search_by_index(engine: ProcessEngine):
    first = engine.runtime.start_process(
        "idx_flow", start_user="u_s", variables={"order_no": "A-1", "amount": 100}
    )
    second = engine.runtime.start_process(
        "idx_flow", start_user="u_s", variables={"order_no": "A-2", "amount": 900}
    )
    with engine.session_factory() as session:
        assert set(instances_by_variable(session, "order_no", text="A-1")) == {first}
        assert set(instances_by_variable(session, "amount", number_gt=500.0)) == {second}
        assert set(instances_by_variable(session, "amount", number=100.0)) == {first}


def test_searchable_type_restricted_by_validator():
    """C.5：object/list 标 searchable → validate 报错。"""
    from flowork.definitions.validator import validate_definition
    from flowork.runtime.registry import create_registry

    definition = FlowBuilder("idx_bad").start().end().connect("start", "end").build()
    definition.variables = [ProcessVariable(name="blob", type="list", searchable=True)]
    issues = validate_definition(definition, create_registry())
    assert "variables.searchable_type" in {i.rule for i in issues}
