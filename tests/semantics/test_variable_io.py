"""变量 I/O 批（kernel-v2 spec §11 / variable-io-api.md）：外部读写命令测试。

验收：三门槛（白名单/类型/终态）+ 合并语义 + variables_updated 事件 +
留痕/索引同事务一致 + 读方法与血缘查询。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.contract import InputContractError
from flowork.definitions.definition import ProcessVariable
from flowork.engine.engine import ProcessEngine
from flowork.errors import InstanceNotFoundError
from flowork.persistence.orm import EventOutboxRow, VariableHistoryRow, VariableIndexRow


@pytest.fixture
def engine() -> ProcessEngine:
    engine = ProcessEngine()
    definition = (
        FlowBuilder("io_flow")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    definition.variables = [
        ProcessVariable(name="amount", type="number", sample=100, searchable=True),
        ProcessVariable(name="memo", type="string", sample=""),
    ]
    definition.input = {
        "type": "object",
        "properties": {"score": {"type": "number", "minimum": 0}},
    }
    engine.repository.deploy(definition)
    return engine


def start(engine: ProcessEngine) -> str:
    return engine.runtime.start_process(
        "io_flow",
        start_user="u_s",
        variables={"amount": 100, "memo": "m", "runtime_key": "v", "score": 5},
    )


def outbox_payload(engine: ProcessEngine, instance_id: str, name: str) -> dict | None:
    with engine.session_factory() as session:
        row = session.scalars(
            select(EventOutboxRow).where(
                EventOutboxRow.instance_id == instance_id, EventOutboxRow.event_name == name
            )
        ).first()
    return row.payload if row else None


def history_count(engine: ProcessEngine, instance_id: str, name: str) -> int:
    with engine.session_factory() as session:
        rows = session.scalars(
            select(VariableHistoryRow).where(
                VariableHistoryRow.instance_id == instance_id,
                VariableHistoryRow.name == name,
            )
        ).all()
    return len(rows)


# —— 白名单与校验 ————————————————————————————————————————————


def test_update_declared_and_existing_keys(engine: ProcessEngine):
    """声明键（variables[]/input schema）与实例既有键（运行期写入）都可改；
    返回合并后的变量全集。"""
    instance_id = start(engine)
    result = engine.runtime.update_variables(
        instance_id, {"amount": 999, "runtime_key": "w", "score": 8}, actor="u_api"
    )
    assert result["amount"] == 999
    assert result["runtime_key"] == "w"
    assert result["score"] == 8
    assert result["memo"] == "m"  # 未给的键不动


def test_reject_undeclared_key(engine: ProcessEngine):
    instance_id = start(engine)
    with pytest.raises(InputContractError, match="未声明"):
        engine.runtime.update_variables(instance_id, {"brand_new": 1}, actor="u_api")


def test_reject_reserved_keys(engine: ProcessEngine):
    instance_id = start(engine)
    for key in ("initiator", "task"):
        with pytest.raises(InputContractError, match="保留标识符"):
            engine.runtime.update_variables(instance_id, {key: "x"}, actor="u_api")


def test_reject_declared_type_mismatch(engine: ProcessEngine):
    instance_id = start(engine)
    with pytest.raises(InputContractError, match="类型不符"):
        engine.runtime.update_variables(instance_id, {"amount": "big"}, actor="u_api")
    with pytest.raises(InputContractError, match="类型不符"):  # bool 不是 number
        engine.runtime.update_variables(instance_id, {"amount": True}, actor="u_api")


def test_input_contract_boundary_on_external_update(engine: ProcessEngine):
    """合并结果过 input 契约（boundary=external_update）：score=-5 违反 minimum 拒收。"""
    instance_id = start(engine)
    with pytest.raises(InputContractError):
        engine.runtime.update_variables(instance_id, {"score": -5}, actor="u_api")


def test_reject_oversized_updates(engine: ProcessEngine):
    instance_id = start(engine)
    blob = {"memo": "x" * (300 * 1024)}
    with pytest.raises(InputContractError, match="256KB"):
        engine.runtime.update_variables(instance_id, blob, actor="u_api")


# —— 状态门槛与幂等 ————————————————————————————————————————


def test_terminal_rejected_and_paused_frozen(engine: ProcessEngine):
    """终态拒写（既有）；暂停＝冻结快照同样拒写（v2.3 第二十轮·审计 D2）——
    修数据路径＝resume → 改 → 再 pause。"""
    instance_id = start(engine)
    engine.runtime.pause_process(instance_id, actor="u_s")
    with pytest.raises(InstanceNotFoundError, match="冻结快照"):
        engine.runtime.update_variables(instance_id, {"amount": 200}, actor="u_api")
    engine.runtime.resume_process(instance_id, actor="u_s")
    engine.runtime.update_variables(instance_id, {"amount": 200}, actor="u_api")  # 恢复后可改

    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor="u_a")  # 流程走完 → 终态
    with pytest.raises(InstanceNotFoundError, match="终态"):
        engine.runtime.update_variables(instance_id, {"amount": 300}, actor="u_api")


def test_replay_same_body_converges(engine: ProcessEngine):
    """同 body 重放收敛：第二次改值不再产生历史行（值相等跳过）。"""
    instance_id = start(engine)
    engine.runtime.update_variables(instance_id, {"amount": 500}, actor="u_api")
    engine.runtime.update_variables(instance_id, {"amount": 500}, actor="u_api")
    # birth（None→100）+ 首次变化（100→500）= 2 行；重放不追加
    assert history_count(engine, instance_id, "amount") == 2
    assert engine.runtime.variables_of(instance_id)["amount"] == 500


# —— 事件 / 索引 / 留痕同事务 ——————————————————————————————


def test_event_emitted_with_changes_and_reason(engine: ProcessEngine):
    instance_id = start(engine)
    engine.runtime.update_variables(instance_id, {"amount": 999}, actor="u_api", reason="风控刷新")
    payload = outbox_payload(engine, instance_id, "variables_updated")
    assert payload is not None
    assert payload["actor"] == "u_api"
    assert payload["reason"] == "风控刷新"
    assert payload["changes"] == [{"name": "amount", "old": 100, "new": 999}]


def test_searchable_index_consistent_same_transaction(engine: ProcessEngine):
    instance_id = start(engine)
    engine.runtime.update_variables(instance_id, {"amount": 999}, actor="u_api")
    with engine.session_factory() as session:
        row = session.scalars(
            select(VariableIndexRow).where(
                VariableIndexRow.instance_id == instance_id,
                VariableIndexRow.name == "amount",
            )
        ).one()
    assert row.value_num == 999.0  # 索引与变量同命令同事务


# —— 读面 ————————————————————————————————————————————————————


def test_variables_of_unknown_instance_raises(engine: ProcessEngine):
    with pytest.raises(InstanceNotFoundError):
        engine.runtime.variables_of("no-such-id")


def test_variable_history_query_filters_by_name(engine: ProcessEngine):
    instance_id = start(engine)
    engine.runtime.update_variables(instance_id, {"amount": 999}, actor="u_api")
    records = engine.runtime.variable_history(instance_id, name="amount")
    # 倒序：最新在前——外部改写在 birth 之前
    assert [(r.name, r.old_value, r.new_value, r.source, r.acted_by) for r in records] == [
        ("amount", 100, 999, "external_update", "u_api"),
        ("amount", None, 100, "engine", None),
    ]
    assert engine.runtime.variable_history(instance_id, name="memo")  # 其他键不受影响
