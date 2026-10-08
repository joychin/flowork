"""CEL 词汇全链路（kernel-v2 §4.3/§9）：ExpressionFunctions 注册 → 连线公式路由。

覆盖三条主路径：
- 带注册表：``has_role(initiator, …)`` 参与路由决策（initiator 保留标识符注入）；
- 无注册表：同公式求值错 → 技术故障 → 实例挂起 + incident（错误二分，spec §8）；
- 静态检查联动：validate 期对照注册表签名拦截未注册/参数个数不符。
"""

from __future__ import annotations

import pytest

from flowork import contrib
from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable
from flowork.definitions.validator import IssueLevel, validate_definition
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from flowork.participants.org_directory import OrgDirectory

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
    return ProcessEngine(EngineConfig(org=ORG, expression_functions=contrib.cel_vocabulary(ORG)))


def _role_gate_flow() -> ProcessDefinition:
    definition = (
        FlowBuilder("cel_gate")
        .start()
        .branch("gate")
        .user_task("lead_review", assignee="u_bob")
        .user_task("normal_review", assignee="u_zhang")
        .end()
        .connect("start", "gate")
        .flow(
            "gate",
            "lead_review",
            condition='has_role(initiator, "eng_lead")',
        )
        .flow("gate", "normal_review", default=True)
        .connect("lead_review", "end")
        .connect("normal_review", "end")
        .build()
    )
    definition.variables = [ProcessVariable(name="amount", type="number")]
    return definition


def _publish(engine: ProcessEngine, definition: ProcessDefinition) -> None:
    engine.repository.deploy(definition)


def test_formula_with_registered_vocabulary_routes(engine: ProcessEngine):
    """注册词汇参与路由：eng_lead 发起走 lead_review，其他人走默认边。"""
    _publish(engine, _role_gate_flow())
    lead = engine.runtime.start_process("cel_gate", start_user="u_bob")
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=lead)] == ["lead_review"]
    normal = engine.runtime.start_process("cel_gate", start_user="u_zhang")
    assert [t.activity_code for t in engine.task.list_tasks(instance_id=normal)] == [
        "normal_review"
    ]


def test_formula_unknown_function_without_registry_raises_incident():
    """无注册表调未注册函数：求值错＝技术故障（spec §8）。

    启动命令内失败：命令回滚（实例行不落库，get 抛 NotFound），
    故障单由独立事务记录（open，带 instance_id 归因）——启动期引擎既有语义。
    在途节点的公式失败则挂起实例（错误二分主路径，见 incident_service）。
    """
    from flowork.engine.kernel import EngineExecutionError
    from flowork.errors import InstanceNotFoundError

    bare = ProcessEngine(EngineConfig(org=ORG))  # 未配 expression_functions
    _publish(bare, _role_gate_flow())
    with pytest.raises(EngineExecutionError, match="公式求值失败"):
        bare.runtime.start_process("cel_gate", start_user="u_bob")
    incidents = bare.runtime.list_incidents()
    assert incidents, "公式求值错应产生故障单"
    assert incidents[0]["state"] == "open"
    with pytest.raises(InstanceNotFoundError):
        bare.runtime.get_instance(incidents[0]["instance_id"])


def test_validate_catches_unregistered_function_with_registry(engine: ProcessEngine):
    """设计期静态拦截：注册表已配，公式调用未注册函数/参数不符 → ERROR。"""
    definition = _role_gate_flow()
    bad = _role_gate_flow()
    # 直接改公式：未注册函数
    bad.flows = [
        flow.model_copy(
            update={
                "condition": (
                    "nosuchfn(initiator)" if flow.condition is not None else flow.condition
                )
            }
        )
        if flow.condition is not None
        else flow
        for flow in definition.flows
    ]
    issues = validate_definition(
        bad,
        engine.config.node_registry,
        expression_functions=engine.config.expression_functions,
    )
    unknown = [i for i in issues if i.rule == "condition.function.unknown"]
    assert unknown and unknown[0].level == IssueLevel.ERROR


def test_validate_accepts_registered_vocabulary(engine: ProcessEngine):
    """合法词汇公式：静态检查零 ERROR（发布畅通）。"""
    issues = validate_definition(
        _role_gate_flow(),
        engine.config.node_registry,
        expression_functions=engine.config.expression_functions,
    )
    assert not [i for i in issues if i.level == IssueLevel.ERROR]


def test_cel_role_subject_rejected_at_runtime(engine: ProcessEngine):
    """manager_of 吃 role: 主体：运行期报错（设计期已由类型词汇约束）。"""
    from flowork.expr.cel import FormulaError, evaluate_formula

    table = {name: item.fn for name, item in engine.config.expression_functions.functions().items()}
    with pytest.raises((FormulaError, FloworkError), match="角色主体"):
        evaluate_formula('manager_of("role:finance") == "u_bob"', {}, functions=table)
