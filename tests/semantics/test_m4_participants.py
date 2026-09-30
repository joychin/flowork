"""M4 语义测试：组织目录 / 取人函数 / 参与者策略矩阵 / 委托 / 无参与者兜底。"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.engine.kernel import NoParticipantError
from flowork.participants.functions import UnknownFunction, builtin_functions, parse_expression
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
    """无委托的组织目录（委托语义由专门用例验证）。"""
    return ProcessEngine(EngineConfig(org=OrgDirectory.from_dict(_org_data_no_delegation())))


def publish(engine: ProcessEngine, definition) -> None:
    engine.repository.save_definition(definition)
    engine.repository.publish(definition.code)


def complete_first(engine: ProcessEngine, instance_id: str, actor: str) -> None:
    """办理实例当前第一件待办。"""
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task.id, actor=actor)


def run_flow(engine: ProcessEngine, flow: FlowBuilder, variables: dict | None = None) -> str:
    publish(engine, flow.build())
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


# —— 取人函数（9 例）——


def test_parse_expression_function():
    call = parse_expression("users_with_role('finance')")
    assert call is not None and call.name == "users_with_role" and call.args == ["finance"]


def test_parse_expression_literal():
    assert parse_expression("u_alice") is None


def test_unknown_function_raises(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("f_bad")
        .start()
        .user_task("a", assignee="no_such_fn()")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    with pytest.raises(UnknownFunction):
        run_flow(org_engine, flow)


def test_function_originator(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("f1")
        .start()
        .user_task("a", assignee="originator()")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)
    tasks = org_engine.task.list_tasks(instance_id=instance_id)
    assert [t.assignee for t in tasks] == ["u_zhang"]


def test_function_manager(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("f2")
        .start()
        .user_task("a", assignee="manager()")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)
    assert [t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id)] == ["u_bob"]


def test_function_dept_head_default(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("f3")
        .start()
        .user_task("a", assignee="dept_head()")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)  # 发起人在 d_eng → 主管 u_bob
    assert [t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id)] == ["u_bob"]


def test_function_users_with_role(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("f4")
        .start()
        .user_task("a", assignees=["users_with_role('finance')"])
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow)
    assignees = sorted(t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id))
    assert assignees == ["u_fin1", "u_fin2"]


def test_function_variable_argument(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("f5")
        .start()
        .user_task("a", assignee="manager_of('applicant')")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    instance_id = run_flow(org_engine, flow, variables={"applicant": "u_dave"})
    assert [t.assignee for t in org_engine.task.list_tasks(instance_id=instance_id)] == ["u_bob"]


def test_function_previous_handlers(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("f6")
        .start()
        .user_task("a", assignee="u_bob")
        .user_task("b", assignee="previous_handlers()")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
    )
    instance_id = run_flow(org_engine, flow)
    a_task = org_engine.task.list_tasks(instance_id=instance_id)[0]
    org_engine.task.complete(a_task.id, actor="u_bob")
    b_task = org_engine.task.list_tasks(instance_id=instance_id)[0]
    assert b_task.assignee == "u_bob"  # 上一节点办理人


# —— 策略矩阵（8 例）——


def test_strategy_auto_pass_originator(org_engine: ProcessEngine):
    flow = (
        FlowBuilder("s1")
        .start()
        .user_task("a", assignees=["u_zhang", "u_bob"], auto_pass_originator=True)
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
        .user_task("a", assignees=["u_zhang"], auto_pass_originator=True)
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
        .user_task("a", assignee="u_bob")
        .user_task("b", assignee="u_bob", auto_pass_previous=True)
        .user_task("c", assignee="u_ceo")
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
        .user_task("a", assignee="u_ceo")
        .user_task("b", assignee="u_ceo", auto_pass_participated=True)
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
        .user_task("a", assignee="u_bob")
        .user_task("b", assignee="u_bob")
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
        .user_task("a", candidate_users=["u_x", "u_y"])
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
        .user_task("a", assignee="u_bob")
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
        .user_task("a", assignees=["u_bob", "u_fin1"])
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
        .user_task("a", assignee="u_bob")
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
        .user_task("a", assignee="u_bob")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    publish(engine, flow.build())
    instance_id = engine.runtime.start_process("d4", start_user="u_zhang")
    assert [t.assignee for t in engine.task.list_tasks(instance_id=instance_id)] == ["u_bob"]


# —— 简化模式回退（无组织目录，2 例）——


def test_simple_fallback_without_org():
    engine = ProcessEngine()
    flow = (
        FlowBuilder("x1")
        .start()
        .user_task("a", assignee="originator")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    publish(engine, flow.build())
    instance_id = engine.runtime.start_process("x1", start_user="u_me")
    assert [t.assignee for t in engine.task.list_tasks(instance_id=instance_id)] == ["u_me"]


def test_functions_registry_custom():
    functions = builtin_functions()
    functions.register("pick_boss", lambda ctx, org: ["u_chair"], replace=True)
    assert "pick_boss" in functions.names()
