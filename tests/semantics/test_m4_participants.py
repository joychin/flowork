"""M4 语义测试：组织目录 / 取人公式（单轨 CEL）/ 参与者策略矩阵 / 委托 / 兜底。

位点统一（spec §4.2）：assignee/assignees 每项为 CEL 表达式——字面人写
字符串字面量 ``'u_a'``，函数/变量引用如 ``manager_of(initiator)``。
求值错（未注册函数/语法/类型）按错误二分包 EngineExecutionError（挂起+故障单）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowork import contrib
from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.definitions.node import MultiInstance
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.engine.kernel import EngineExecutionError, NoParticipantError
from flowork.participants.org_directory import OrgDirectory

ORG_DATA = {
    "users": {
        "u_zhang": {"manager": "u_bob", "department": "d_eng"},
        "u_bob": {"manager": "u_ceo", "department": "d_eng", "roles": ["eng_lead"]},
        "u_ceo": {"department": "d_all"},
        "u_dave": {"manager": "u_bob", "department": "d_eng"},
        "u_fin1": {"manager": "u_ceo", "department": "d_fin", "roles": ["finance"]},
        "u_fin2": {"manager": "u_ceo", "department": "d_fin", "roles": ["finance"]},
    },
    "departments": {
        "d_eng": {"head": "u_bob", "parent": "d_all"},
        "d_fin": {"head": "u_ceo", "parent": "d_all"},
        "d_all": {"head": "u_ceo"},
    },
    "delegations": [{"from": "u_bob", "to": "u_dave", "until": "2099-12-31"}],
    "admins": ["u_admin"],
}

ORG = OrgDirectory.from_dict(ORG_DATA)


def _org_data_no_delegation() -> dict:
    data = dict(ORG_DATA)
    data["delegations"] = []
    return data


@pytest.fixture
def org_engine() -> ProcessEngine:
    """无委托的组织目录 + contrib CEL 词汇（委托语义由专门用例验证）。"""
    return ProcessEngine(
        EngineConfig(
            org=OrgDirectory.from_dict(_org_data_no_delegation()),
            expression_functions=contrib.cel_vocabulary(
                OrgDirectory.from_dict(_org_data_no_delegation())
            ),
        )
    )


def publish(engine: ProcessEngine, definition) -> None:
    engine.repository.deploy(definition)


def complete_first(engine: ProcessEngine, instance_id: str, actor: str) -> None:
    """办理实例当前第一件待办。"""
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor=actor)


def run_flow(
    engine: ProcessEngine,
    flow: FlowBuilder,
    variables: dict | None = None,
    declared: list[ProcessVariable] | None = None,
) -> str:
    definition = (
        flow.build().model_copy(update={"variables": declared}) if declared else flow.build()
    )
    publish(engine, definition)
    return engine.runtime.start_process(flow._code, start_user="u_zhang", variables=variables or {})


# —— 组织目录（10 例）——


def test_org_manager_chain():
    assert ORG.manager_of("u_zhang") == "u_bob"
    assert ORG.manager_of("u_ceo") is None


def test_org_department_head():
    assert ORG.department_head("d_eng") == "u_bob"
    assert ORG.department_head("d_none") is None


def test_org_parent_department():
    assert ORG.parent_department("d_eng") == "d_all"


def test_org_users_with_role():
    assert set(ORG.users_with_role("finance")) == {"u_fin1", "u_fin2"}
    assert ORG.users_with_role("nope") == []


def test_org_users_in_department_recursive():
    users = ORG.users_in_department("d_all", recursive=True)
    assert {"u_zhang", "u_bob", "u_fin1"} <= set(users)


def test_org_users_in_department_flat():
    assert set(ORG.users_in_department("d_eng")) == {"u_zhang", "u_bob", "u_dave"}


def test_org_delegation_active():
    assert ORG.delegation_of("u_bob") == "u_dave"


def test_org_delegation_absent():
    assert ORG.delegation_of("u_zhang") is None


def test_org_delegation_expired():
    org = OrgDirectory.from_dict(
        {"delegations": [{"from": "u_bob", "to": "u_dave", "until": "2020-01-01"}]}
    )
    assert org.delegation_of("u_bob") is None


def test_org_from_yaml(examples_dir: Path):
    org = OrgDirectory.from_yaml(examples_dir / "org.yaml")
    assert org.manager_of("u_zhang") == "u_bob"
    assert org.admins == ["u_admin"]


# —— 取人公式（单轨 CEL：contrib 三词汇 + 宿主自定义）——


def test_registry_empty_by_default_without_vocabulary():
    """内核注册表出厂为空——目录在而词汇未挂时，公式调用报技术故障（挂起+故障单）。"""
    engine = ProcessEngine(EngineConfig(org=OrgDirectory.from_dict(_org_data_no_delegation())))
    flow = (
        FlowBuilder("f_empty")
        .start()
        .user_task("a", assignee="manager_of(initiator)")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    with pytest.raises(EngineExecutionError, match="取人公式求值失败"):
        run_flow(engine, flow)


def test_unknown_function_raises(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("f_bad")
        .start()
        .user_task("a", assignee="no_such_fn(initiator)")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    with pytest.raises(EngineExecutionError, match="取人公式求值失败"):
        run_flow(org_engine, flow)


def test_initiator_identifier_resolves_start_user(org_engine: ProcessEngine):
    """发起人＝CEL 保留标识符 initiator（原 originator 魔法词，位点统一后退役）。"""
    flow = (
        FlowBuilder("f1")
        .start()
        .user_task("a", assignee="initiator")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)
    tasks = org_engine.task.list_tasks(instance_id=instance_id)
    assert [t.assignee for t in tasks] == ["u_zhang"]


def test_function_manager_of_initiator(org_engine: ProcessEngine):
    """manager_of(initiator)＝发起人的上级。"""
    flow = (
        FlowBuilder("f2")
        .start()
        .user_task("a", assignee="manager_of(initiator)")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)
    assert [t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id)] == ["u_bob"]


def test_contrib_manager_of_rejects_role_subject(org_engine: ProcessEngine):
    """manager_of 吃 role: 主体 → 技术故障（设计期语义错误，spec §5.5）。"""
    flow = (
        FlowBuilder("f_role")
        .start()
        .user_task("a", assignee="manager_of(x)")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    with pytest.raises(EngineExecutionError):
        run_flow(
            org_engine,
            flow,
            variables={"x": "role:finance"},
            declared=[ProcessVariable(name="x", type="string")],
        )


def test_contrib_manager_of_org_subject_returns_dept_head(org_engine: ProcessEngine):
    """manager_of 吃 org: 主体 → 部门负责人（spec §5.5）。"""
    flow = (
        FlowBuilder("f_org")
        .start()
        .user_task("a", assignee="manager_of(x)")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(
        org_engine,
        flow,
        variables={"x": "org:d_eng"},
        declared=[ProcessVariable(name="x", type="string")],
    )
    assert [t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id)] == ["u_bob"]


def test_function_users_with_role(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("f4")
        .start()
        .user_task(
            "a",
            assignees=['users_with_role("finance")'],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)
    assignees = sorted(t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id))
    assert assignees == ["u_fin1", "u_fin2"]


def test_function_variable_argument(org_engine: ProcessEngine):
    """标识符实参＝变量引用（manager_of(applicant)）。"""
    flow = (
        FlowBuilder("f5")
        .start()
        .user_task("a", assignee="manager_of(applicant)")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(
        org_engine,
        flow,
        variables={"applicant": "u_dave"},
        declared=[ProcessVariable(name="applicant", type="string")],
    )
    assert [t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id)] == ["u_bob"]


def test_mixed_literal_and_function_expressions(org_engine: ProcessEngine):
    """字面人与函数混排：str 与 list 结果归并入池。"""
    flow = (
        FlowBuilder("f_mix")
        .start()
        .user_task(
            "a",
            assignee="'u_ceo'",
            assignees=['users_with_role("finance")', "manager_of(initiator)"],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)
    assignees = sorted(t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id))
    assert assignees == ["u_bob", "u_ceo", "u_fin1", "u_fin2"]


def test_predicate_result_rejected_in_participant_position(org_engine: ProcessEngine):
    """谓词函数误用取人位（bool 结果）→ NoParticipantError 兜底拒绝。"""
    flow = (
        FlowBuilder("f_pred")
        .start()
        .user_task("a", assignee='has_role(initiator, "eng_lead")')
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    with pytest.raises(NoParticipantError, match="谓词"):
        run_flow(org_engine, flow)


def test_none_result_propagates_as_empty(org_engine: ProcessEngine):
    """查无传播（spec §5.5）：表达式求值为 null 贡献空，不报错；空池走兜底。"""
    flow = (
        FlowBuilder("f_none")
        .start()
        .user_task("a", assignee='manager_of("u_ceo")', no_participant_policy="to_admins")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)
    assert [t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id)] == ["u_admin"]


class _HostVocabulary:
    """宿主自定义词汇注册演示（ExpressionFunctions 端口的最小实现）。"""

    def __init__(self, org: OrgDirectory) -> None:
        from flowork.spi.ports import ExpressionFunction, FunctionSignature

        def dept_head(user: str) -> str | None:
            dept = org.department_of(user) or ""
            return org.department_head(dept)

        self._table = {
            "dept_head": ExpressionFunction(FunctionSignature(("one_user",), "one_user"), dept_head)
        }

    def functions(self):
        return self._table


def test_host_registered_vocabulary_dept_head(org_engine: ProcessEngine):
    """宿主自定义纯读闭包词汇：dept_head(initiator)（发起人部门主管）。"""
    org_engine.config.expression_functions = _HostVocabulary(
        OrgDirectory.from_dict(_org_data_no_delegation())
    )
    flow = (
        FlowBuilder("f3")
        .start()
        .user_task("a", assignee="dept_head(initiator)")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)  # 发起人 u_zhang 在 d_eng → 主管 u_bob
    assert [t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id)] == ["u_bob"]


# —— 策略矩阵（8 例）——


def test_strategy_auto_pass_originator(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("s1")
        .start()
        .user_task("a", assignees=["'u_zhang'", "'u_bob'"], auto_pass_originator=True)
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)
    tasks = org_engine.task.list_tasks(instance_id=instance_id)
    assert [t.assignee for t in tasks] == ["u_bob"]  # 发起人自动通过
    history = [
        (h.activity_code, h.assignee, h.outcome)
        for h in org_engine.history.task_history(instance_id)
    ]
    assert ("a", "u_zhang", "auto_pass") in history
    token = next(t for t in org_engine.history.tokens(instance_id) if t.activity_code == "a")
    assert (token.item_count, token.approve_count) == (2, 1)


def test_strategy_all_auto_pass_completes_node(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("s2")
        .start()
        .user_task("a", assignees=["'u_zhang'"], auto_pass_originator=True)
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)
    assert org_engine.runtime.get_instance(instance_id).state == "completed"


def test_strategy_auto_pass_previous(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("s3")
        .start()
        .user_task("a", assignee="'u_bob'")
        .user_task("b", assignee="'u_bob'", auto_pass_previous=True)
        .user_task("c", assignee="'u_ceo'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "c")
        .connect("c", "end")
    )
    instance_id = run_flow(org_engine, flow)
    complete_first(org_engine, instance_id, "u_bob")
    # b 节点 u_bob 已在 a 办过 → 自动通过，直接到 c
    tasks = [t.activity_code for t in org_engine.task.list_tasks(instance_id=instance_id)]
    assert tasks == ["c"]


def test_strategy_auto_pass_participated(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("s4")
        .start()
        .user_task("a", assignee="'u_ceo'")
        .user_task("b", assignee="'u_ceo'", auto_pass_participated=True)
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
    )
    instance_id = run_flow(org_engine, flow)
    complete_first(org_engine, instance_id, "u_ceo")
    assert org_engine.runtime.get_instance(instance_id).state == "completed"


def test_strategy_off_by_default(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("s5")
        .start()
        .user_task("a", assignee="'u_bob'")
        .user_task("b", assignee="'u_bob'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
    )
    instance_id = run_flow(org_engine, flow)
    complete_first(org_engine, instance_id, "u_bob")
    assert [t.activity_code for t in org_engine.task.list_tasks(instance_id=instance_id)] == ["b"]


def test_no_participant_raises_by_default(org_engine: ProcessEngine):
    flow = FlowBuilder("s6").start().user_task("a").end().connect("start", "a").connect("a", "end")
    with pytest.raises(NoParticipantError):
        run_flow(org_engine, flow)


def test_no_participant_to_admins(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("s7")
        .start()
        .user_task("a", no_participant_policy="to_admins")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)
    assert [t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id)] == ["u_admin"]


def test_claimable_when_only_candidates_no_org():
    engine = ProcessEngine()  # 无组织目录：候选任务仍可认领
    flow = (
        FlowBuilder("s8")
        .start()
        .user_task("a", candidate_users=["'u_x'", "'u_y'"])
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    publish(engine, flow.build())
    instance_id = engine.runtime.start_process("s8", start_user="u_s")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.claim(task.id, "u_y")
    engine.task.complete(task.id, actor="u_y")
    assert engine.runtime.get_instance(instance_id).state == "completed"


# —— 委托（4 例）——


def test_delegation_replaces_assignee():
    engine = ProcessEngine(EngineConfig(org=OrgDirectory.from_dict(ORG_DATA)))
    flow = (
        FlowBuilder("d1")
        .start()
        .user_task("a", assignee="'u_bob'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    publish(engine, flow.build())
    instance_id = engine.runtime.start_process("d1", start_user="u_zhang")
    tasks = engine.task.list_tasks(instance_id=instance_id)
    assert [t.assignee for t in tasks] == ["u_dave"]  # u_bob 委托给 u_dave


def test_delegation_in_multi_instance():
    engine = ProcessEngine(EngineConfig(org=OrgDirectory.from_dict(ORG_DATA)))
    flow = (
        FlowBuilder("d2")
        .start()
        .user_task(
            "a",
            assignees=["'u_bob'", "'u_fin1'"],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    publish(engine, flow.build())
    instance_id = engine.runtime.start_process("d2", start_user="u_zhang")
    assignees = sorted(t.assignee for t in engine.task.list_tasks(instance_id=instance_id))
    assert assignees == ["u_dave", "u_fin1"]


def test_delegation_expired_keeps_assignee():
    data = dict(ORG_DATA)
    data["delegations"] = [{"from": "u_bob", "to": "u_dave", "until": "2020-01-01"}]
    engine = ProcessEngine(EngineConfig(org=OrgDirectory.from_dict(data)))
    flow = (
        FlowBuilder("d3")
        .start()
        .user_task("a", assignee="'u_bob'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    publish(engine, flow.build())
    instance_id = engine.runtime.start_process("d3", start_user="u_zhang")
    assert [t.assignee for t in engine.task.list_tasks(instance_id=instance_id)] == ["u_bob"]


def test_delegation_not_configured_is_noop():
    data = dict(ORG_DATA)
    data["delegations"] = []
    engine = ProcessEngine(EngineConfig(org=OrgDirectory.from_dict(data)))
    flow = (
        FlowBuilder("d4")
        .start()
        .user_task("a", assignee="'u_bob'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    publish(engine, flow.build())
    instance_id = engine.runtime.start_process("d4", start_user="u_zhang")
    assert [t.assignee for t in engine.task.list_tasks(instance_id=instance_id)] == ["u_bob"]


# —— 无目录路径（2 例：同一条 CEL 求值路径，仅无委托/兜底）——


def test_cel_without_org():
    engine = ProcessEngine()
    flow = (
        FlowBuilder("x1")
        .start()
        .user_task("a", assignee="initiator")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    publish(engine, flow.build())
    instance_id = engine.runtime.start_process("x1", start_user="u_me")
    assert [t.assignee for t in engine.task.list_tasks(instance_id=instance_id)] == ["u_me"]


def test_bare_id_without_quotes_is_design_time_error():
    """字面裸 id（未引号）＝未声明变量——设计期即拦（v2.3 第二十轮 D6-b 收紧）。

    此前空声明豁免把它推迟到运行期挂起；现在图纸零声明同样做引用检查，
    发布即 InvalidDefinition（语义前移：写错位置的人在画图时就能看到）。"""
    from flowork.definitions.validator import InvalidDefinition

    engine = ProcessEngine()
    flow = (
        FlowBuilder("x2")
        .start()
        .user_task("a", assignee="u_bob")  # 未引号的裸 id：标识符=变量，非字面人
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    with pytest.raises(InvalidDefinition, match="u_bob"):
        publish(engine, flow.build())
