"""P0-6（kernel-v2 spec §3.2/§16）：input 契约三边界校验测试。

验收：越界拒收——发起缺 required／任务 output 破坏类型／子流程输出映射越界，
全部报错不入库；无契约定义零影响。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.contract import InputContractError
from flowork.engine.engine import ProcessEngine
from flowork.persistence.orm import ProcessInstanceRow

SCHEMA = {
    "type": "object",
    "properties": {
        "amount": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["amount", "reason"],
}


def _engine_with_contract() -> ProcessEngine:
    engine = ProcessEngine()
    definition = (
        FlowBuilder("contract_flow")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    definition.input = SCHEMA
    engine.repository.deploy(definition)
    return engine


def instance_count(engine: ProcessEngine) -> int:
    with engine.session_factory() as session:
        return len(session.scalars(select(ProcessInstanceRow)).all())


def test_start_rejects_missing_required():
    engine = _engine_with_contract()
    with pytest.raises(InputContractError, match="start"):
        engine.runtime.start_process(
            "contract_flow",
            start_user="u_s",
            variables={"amount": 100},  # 缺 reason
        )
    assert instance_count(engine) == 0  # 报错不入库


def test_start_rejects_wrong_type():
    engine = _engine_with_contract()
    with pytest.raises(InputContractError):
        engine.runtime.start_process(
            "contract_flow",
            start_user="u_s",
            variables={"amount": "一百", "reason": "差旅"},  # amount 应为 number
        )
    assert instance_count(engine) == 0


def test_task_output_rejects_contract_break():
    engine = _engine_with_contract()
    instance_id = engine.runtime.start_process(
        "contract_flow", start_user="u_s", variables={"amount": 100, "reason": "差旅"}
    )
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    with pytest.raises(InputContractError, match="task_output"):
        engine.task.complete(
            task.id,
            actor="u_a",
            variables={"amount": "贰百"},  # 类型破坏
        )
    # 任务仍在、变量未污染
    assert engine.task.list_tasks(instance_id=instance_id, assignee="u_a")
    assert engine.runtime.get_instance(instance_id).state == "running"


def test_task_output_compatible_merge_passes():
    engine = _engine_with_contract()
    instance_id = engine.runtime.start_process(
        "contract_flow", start_user="u_s", variables={"amount": 100, "reason": "差旅"}
    )
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a", variables={"amount": 200})  # 兼容覆盖
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)] == ["b"]


def test_definition_without_contract_unaffected():
    """无 input 契约：三边界零校验（存量流程不受影响）。"""
    engine = ProcessEngine()
    definition = (
        FlowBuilder("free_flow")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("free_flow", start_user="u_s", variables={})
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a", variables={"anything": "任意"})
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_subprocess_output_mapping_boundary(tmp_path):
    """边界③：子流程输出映射回写破坏父契约 → 拒收（同事务回滚，子任务不落已办）。"""
    engine = ProcessEngine()
    child = (
        FlowBuilder("contract_child")
        .start()
        .user_task("c", assignee="'u_c'")
        .end()
        .connect("start", "c")
        .connect("c", "end")
        .build()
    )
    engine.repository.deploy(child)
    parent = (
        FlowBuilder("contract_parent")
        .start()
        .user_task("a", assignee="'u_a'")
        .call_activity(
            "sub",
            "子流程",
            process_code="contract_child",
            is_synchronous=True,
            output_mappings={"amount": "amount"},  # 子 amount → 父 amount（number）
        )
        .end()
        .connect("start", "a")
        .connect("a", "sub")
        .connect("sub", "end")
        .build()
    )
    parent.input = {
        "type": "object",
        "properties": {"amount": {"type": "number"}},
        "required": ["amount"],
    }
    engine.repository.deploy(parent)

    instance_id = engine.runtime.start_process(
        "contract_parent", start_user="u_s", variables={"amount": 1}
    )
    engine.task.complete(
        engine.task.list_tasks(instance_id=instance_id, assignee="u_a")[0].id, actor="u_a"
    )
    sub_instance = next(
        row.id
        for row in engine.session_factory().scalars(
            select(ProcessInstanceRow).where(ProcessInstanceRow.parent_id == instance_id)
        )
    )
    # 子流程 c 提交把 amount 写成 string → 输出映射破坏父契约 → 拒收
    child_task = engine.task.list_tasks(instance_id=sub_instance)[0]
    with pytest.raises(InputContractError, match="node_output"):
        engine.task.complete(child_task.id, actor="u_c", variables={"amount": "被污染"})
    assert engine.task.list_tasks(instance_id=sub_instance, assignee="u_c")  # 子任务回滚未办

    # 兼容映射（number）放行
    engine.task.complete(child_task.id, actor="u_c", variables={"amount": 2})
    assert engine.runtime.get_instance(instance_id).state == "completed"


def test_reject_variables_respect_contract():
    """REVIEW P1-④回归：驳回携带的 variables 同走 input 契约（任务提交侧输出）。"""
    engine = _engine_with_contract()
    instance_id = engine.runtime.start_process(
        "contract_flow", start_user="u_s", variables={"amount": 100, "reason": "差旅"}
    )
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    with pytest.raises(InputContractError, match="task_output"):
        engine.task.reject(task.id, actor="u_a", variables={"amount": "污染"})
    # 拒收后任务仍在、可正常驳回（驳回生效：a 待办消失）
    engine.task.reject(task.id, actor="u_a")
    assert engine.task.list_tasks(instance_id=instance_id, assignee="u_a") == []
