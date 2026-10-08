"""数值/字符串词汇全链路（能力补足 P0-2）：numeric_vocabulary 注册 → 路由/取人。

覆盖三条主路径：
- 挂包后 ``sum/split`` 等散件参与路由决策（与内置四类条件无缝组合）；
- 与组织词汇包并用（两包 functions() 拍平合并——宿主组合词汇的惯用法）；
- 类型不符 → FormulaError → 技术故障 → 挂起 + incident（错误二分统一语义）。
"""

from __future__ import annotations

import pytest

from flowork import contrib
from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from flowork.participants.org_directory import OrgDirectory
from flowork.spi.ports import ExpressionFunction, FunctionSignature

ORG = OrgDirectory.from_dict(
    {
        "users": {
            "u_zhang": {"manager": "u_bob", "department": "d_eng"},
            "u_bob": {"manager": "u_ceo", "department": "d_eng", "roles": ["eng_lead"]},
        },
        "departments": {"d_eng": {"head": "u_bob"}},
    }
)


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine(EngineConfig(expression_functions=contrib.numeric_vocabulary()))


def _amount_gate_flow() -> ProcessDefinition:
    definition = (
        FlowBuilder("numeric_gate")
        .start()
        .branch("gate")
        .user_task("big_review", assignee="'u_bob'")
        .user_task("normal_review", assignee="'u_zhang'")
        .end()
        .connect("start", "gate")
        .flow("gate", "big_review", condition="sum(items) > 100")
        .flow("gate", "normal_review", default=True)
        .connect("big_review", "end")
        .connect("normal_review", "end")
        .build()
    )
    definition.variables = [ProcessVariable(name="items", type="list")]
    return definition


def test_numeric_vocabulary_routes_on_sum(engine: ProcessEngine):
    """sum 参与路由：列表求和过阈值走重审边。"""
    engine.repository.deploy(_amount_gate_flow())
    iid = engine.runtime.start_process(
        "numeric_gate", start_user="u_s", variables={"items": [60, 50]}
    )
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=iid)] == ["big_review"]

    iid2 = engine.runtime.start_process("numeric_gate", start_user="u_s", variables={"items": [60]})
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=iid2)] == ["normal_review"]


def test_numeric_vocabulary_without_registry_is_tech_fault(engine: ProcessEngine):
    """未挂包的引擎跑同一公式：求值错 → 挂起 + incident（错误二分）。"""
    engine.repository.deploy(_amount_gate_flow())
    bare = ProcessEngine(EngineConfig())
    try:
        bare.repository.deploy(_amount_gate_flow())
        with pytest.raises(FloworkError):
            bare.runtime.start_process(
                "numeric_gate", start_user="u_s", variables={"items": [60, 50]}
            )
        assert bare.runtime.list_incidents()
    finally:
        bare.close()


def test_numeric_and_org_vocabularies_compose():
    """两包并用：functions() 拍平合并（宿主组合词汇的惯用法），条件里同用。"""
    merged = {**contrib.cel_vocabulary(ORG).functions(), **contrib.numeric_vocabulary().functions()}
    engine = ProcessEngine(
        EngineConfig(
            org=ORG,
            expression_functions=_FixedFunctions(merged),
        )
    )
    definition = (
        FlowBuilder("compose_gate")
        .start()
        .branch("gate")
        .user_task("lead_review", assignee="'u_bob'")
        .user_task("normal_review", assignee="users_with_role('eng_lead')[0]")
        .end()
        .connect("start", "gate")
        .flow("gate", "lead_review", condition='has_role(initiator, "eng_lead") && sum(items) > 10')
        .flow("gate", "normal_review", default=True)
        .connect("lead_review", "end")
        .connect("normal_review", "end")
        .build()
    )
    definition.variables = [ProcessVariable(name="items", type="list")]
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process(
        "compose_gate", start_user="u_bob", variables={"items": [20, 5]}
    )
    # u_bob 是 eng_lead 且 sum=25>10 → 走 lead_review
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=iid)] == ["lead_review"]
    engine.close()


def test_numeric_type_mismatch_routes_to_incident(engine: ProcessEngine):
    """类型不符（items 传字符串）→ 技术故障挂起 + incident，不伪装成业务不通过。"""
    engine.repository.deploy(_amount_gate_flow())
    with pytest.raises(FloworkError):
        engine.runtime.start_process(
            "numeric_gate", start_user="u_s", variables={"items": "not-a-list"}
        )
    assert engine.runtime.list_incidents()


class _FixedFunctions:
    """测试用 ExpressionFunctions 最小实现（固定注册表）。"""

    def __init__(self, table):
        self._table = table

    def functions(self):
        return self._table


def test_split_in_subjects_position(engine: ProcessEngine):
    """split 用在取人位：拆分逗号串为多人会签（值位与条件位同一注册面）。"""
    definition = (
        FlowBuilder("split_sign")
        .start()
        .user_task("s", assignees=["split(approvers, ',')[0]", "split(approvers, ',')[1]"])
        .end()
        .connect("start", "s")
        .connect("s", "end")
        .build()
    )
    definition.variables = [ProcessVariable(name="approvers", type="string")]
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process(
        "split_sign", start_user="u_s", variables={"approvers": "u_zhang,u_bob"}
    )
    assignees = sorted(t.assignee for t in engine.task.list_tasks(instance_id=iid))
    assert assignees == ["u_bob", "u_zhang"]


def _unused_signature() -> ExpressionFunction:
    return ExpressionFunction(FunctionSignature(("string",), "string"), lambda s: s)
