"""能力域①组织架构：目录 CRUD/守卫、范围化角色解析、Agent 身份、引擎接缝。"""

from __future__ import annotations

import pytest

from flowork.directory.store import DirectoryError, DirectoryStore
from flowork.engine.engine import ProcessEngine
from flowork.participants.org_directory import OrgDirectory
from tests.suite.features import covers
from tests.suite.walkthrough import complete_of, expect_position


def start_expense(engine: ProcessEngine, user: str, amount: float) -> str:
    return engine.runtime.start_process(
        "suite_expense",
        start_user=user,
        variables={"amount": amount, "expense_type": "travel"},
    )


# —— 目录写面与守卫 ——


@covers("org.unit_crud")
def test_org_unit_crud_guards(org_store: DirectoryStore) -> None:
    with pytest.raises(DirectoryError, match="已存在"):
        org_store.create_org_unit("rd")
    org_store.create_org_unit("rd_qa", name="测试组", parent_code="rd", manager_user_id="u_cto")
    assert org_store.users_in_department("rd", recursive=True)  # 子树可枚举
    assert org_store.parent_department("rd_qa") == "rd"
    org_store.set_org_unit_manager("rd_qa", manager_user_id="u_lead_be")
    assert org_store.department_head("rd_qa") == "u_lead_be"


@covers("org.move_cycle_guard")
def test_move_org_unit_rejects_cycle_and_recomputes_paths(org_store: DirectoryStore) -> None:
    with pytest.raises(DirectoryError, match="环"):
        org_store.move_org_unit("rd", parent_code="rd_be")
    # 合法迁移：会计组并入研发中心，子树路径与人员归属随之变化
    org_store.move_org_unit("fin_gl", parent_code="rd")
    assert "u_chief_acc" in org_store.users_in_department("rd", recursive=True)
    assert "u_chief_acc" not in org_store.users_in_department("fin", recursive=True)


@covers("org.delete_protection")
def test_delete_org_unit_protected_by_children_and_users(org_store: DirectoryStore) -> None:
    with pytest.raises(DirectoryError, match="子部门"):
        org_store.delete_org_unit("rd")
    with pytest.raises(DirectoryError, match="在职用户"):
        org_store.delete_org_unit("rd_be")
    org_store.create_org_unit("rd_tmp", name="临时组", parent_code="rd")
    org_store.delete_org_unit("rd_tmp")  # 空部门可删
    assert org_store.department_head("rd_tmp") is None


@covers("org.role_assign")
def test_role_assignment_guards(org_store: DirectoryStore) -> None:
    with pytest.raises(DirectoryError, match="不存在"):
        org_store.assign_role("u_dev_wu", "ghost_role")
    with pytest.raises(DirectoryError, match="该范围授权"):
        org_store.assign_role("u_cto", "dept_manager", org_unit_code="rd")
    org_store.assign_role("u_dev_wu", "dept_manager", org_unit_code="rd_be")  # 同角色不同范围可叠加
    assert "u_dev_wu" in org_store.users_with_role("dept_manager")


# —— 角色范围化解析（子树→逐级向上→全局，最近命中层胜出）——


@covers("org.scoped_role_resolution")
def test_scoped_role_nearest_level_wins(org_store: DirectoryStore) -> None:
    assert org_store.users_with_role("dept_manager", within="rd_be") == ["u_cto"]
    assert org_store.users_with_role("dept_manager", within="fin_gl") == ["u_cfo"]
    assert org_store.users_with_role("dept_manager") == ["u_cto", "u_cfo", "u_purch_head"]
    # compliance_officer 默认范围挂在采购子树：从后端组向上找不到，落空
    assert org_store.users_with_role("compliance_officer", within="rd_be") == []
    assert org_store.users_with_role("compliance_officer", within="pur") == ["u_compliance"]


@covers("org.is_member_groups")
def test_group_membership_both_reference_kinds(org_store: DirectoryStore) -> None:
    assert org_store.is_member("u_chief_acc", "role:finance_approver")
    assert org_store.is_member("u_chief_acc", "finance_approver")  # 裸名视同角色
    assert not org_store.is_member("u_dev_wu", "role:finance_approver")
    assert org_store.is_member("u_dev_wu", "org:rd_be")
    assert not org_store.is_member("u_dev_wu", "org:rd")
    assert org_store.subjects_in("org:fin_gl") == ["u_chief_acc", "u_cash"]
    assert org_store.subjects_in("role:ceo") == ["u_ceo"]


@covers("org.snapshot")
def test_identity_snapshot(org_store: DirectoryStore, static_engine: ProcessEngine) -> None:
    assert org_store.snapshot("u_dev_wu") == {"department": "rd_be"}
    static_org: OrgDirectory = static_engine.config.org  # type: ignore[assignment]
    assert static_org.snapshot("u_cto") == {"department": "rd", "roles": ["dept_manager"]}
    assert org_store.snapshot("ghost_user") == {}


@covers("org.manager_of_parity")
def test_manager_of_parity_between_materializations(
    org_store: DirectoryStore, static_engine: ProcessEngine
) -> None:
    """两种物化对带前缀主体口径一致（回归：此前持久化版不认 `user:`/`org:`）。

    实测背景（2026-10-09）：静态版剥 `user:`/`agent:` 且认 `org:`，持久化版都不认
    ——同一份图纸换目录实现行为就变。现在两边同口径；人字段写裸 id 仍是推荐写法
    （spec §5.4 v2.3 第十四轮），认前缀是宿主侧的宽容。
    """
    static_org: OrgDirectory = static_engine.config.org  # type: ignore[assignment]
    for subject in ("u_dev_wu", "user:u_dev_wu"):
        assert org_store.manager_of(subject) == "u_lead_be" == static_org.manager_of(subject)
    assert org_store.manager_of("org:rd_be") == "u_lead_be" == static_org.manager_of("org:rd_be")
    assert org_store.manager_of("org:nope") is None
    assert org_store.manager_of("u_nobody") is None


@covers("org.manager_of_agent_owner")
def test_manager_of_resolves_agent_to_owner(org_store: DirectoryStore) -> None:
    """agent 主体 → 负责人（单跳）：问责链 root，不再返回 None。

    单跳不递归——「owner → 所属部门主管 → 上级」是宿主策略，由宿主词汇组合。
    """
    assert org_store.manager_of("agent:ag_expense_bot") == "u_cash"  # owner
    assert org_store.manager_of("ag_expense_bot") == "u_cash"  # 裸 id 回退 agent 查找
    assert org_store.manager_of("agent:ghost_bot") is None


# —— Agent 一等身份 ——


@covers("org.agent_lifecycle")
def test_agent_status_transitions(org_store: DirectoryStore) -> None:
    assert org_store.agent_info("ag_expense_bot").status == "trial"
    org_store.activate_agent("ag_expense_bot")
    assert org_store.agent_info("ag_expense_bot").status == "active"
    org_store.suspend_agent("ag_expense_bot")
    assert org_store.agent_info("ag_expense_bot").status == "suspended"
    org_store.resume_agent("ag_expense_bot")
    assert org_store.agent_info("ag_expense_bot").status == "active"
    assert org_store.agent_owner("ag_expense_bot") == "u_cash"  # 问责链


@covers("org.agent_credential")
def test_agent_credential_issue_verify_revoke(org_store: DirectoryStore) -> None:
    org_store.suspend_agent("ag_expense_bot")
    with pytest.raises(DirectoryError, match="不可签发"):
        org_store.issue_credential("ag_expense_bot")
    org_store.resume_agent("ag_expense_bot")

    token = org_store.issue_credential("ag_expense_bot")
    assert org_store.verify_credential(token) == "ag_expense_bot"
    assert org_store.verify_credential("fwac-invalid-secret") is None  # 不泄露原因
    org_store.revoke_credential(token)
    assert org_store.verify_credential(token) is None


# —— 引擎接缝：目录事实驱动真实流程 ——


@covers("org.manager_reroute", "participant.expression_manager_of")
def test_manager_reshuffle_reroutes_new_instances_only(
    suite_engine: ProcessEngine, org_store: DirectoryStore
) -> None:
    """换主管只影响新实例（在途任务按创建时解析，不迁移）。"""
    first = start_expense(suite_engine, "u_dev_wu", amount=1200)
    expect_position(
        suite_engine, first, nodes={"quick_review"}, tasks={("quick_review", "u_lead_be")}
    )
    org_store.set_org_unit_manager("rd_be", manager_user_id="u_cto")

    second = start_expense(suite_engine, "u_dev_wu", amount=1200)
    expect_position(
        suite_engine,
        second,
        nodes={"quick_review"},
        tasks={("quick_review", "u_cto")},
        note="新实例按新主管解析",
    )
    expect_position(
        suite_engine,
        first,
        nodes={"quick_review"},
        tasks={("quick_review", "u_lead_be")},
        note="在途实例不动",
    )


@covers("participant.expression_users_with_role")
def test_role_expression_expands_in_real_flow(suite_engine: ProcessEngine) -> None:
    """users_with_role 展开为会签名单：财务审批两人各得待办。"""
    iid = start_expense(suite_engine, "u_dev_wu", amount=8000)
    expect_position(
        suite_engine,
        iid,
        nodes={"fin_countersign"},
        tasks={("fin_countersign", "u_chief_acc"), ("fin_countersign", "u_cash")},
    )


@covers("participant.delegation")
def test_delegation_replaces_assignee_in_flow(static_engine: ProcessEngine) -> None:
    """静态目录委托生效：rd_fe 主管赵敏的审批落到受托人王工。"""
    iid = static_engine.runtime.start_process(
        "suite_expense",
        start_user="u_dev_liu",
        variables={"amount": 1200, "expense_type": "travel"},
    )
    expect_position(
        static_engine,
        iid,
        nodes={"quick_review"},
        tasks={("quick_review", "u_lead_be")},
        note="manager_of 解析出 u_lead_fe 后按委托替换为 u_lead_be",
    )
    complete_of(static_engine, iid, "u_lead_be")
    expect_position(static_engine, iid, nodes=set(), state="completed")
