"""候选认领制（A 批）：配目录可用性 / 组引用认领时展开 / 候选查询维度。

来源：2026-10-09 走查发现三缺口——
① 配了组织目录时取人解析兜底先抛错，候选节点不可用（tests/suite/test_orchestration.py
   旧注释「配置 OrgDirectory 时纯候选人节点先走无参与者兜底（内核现状）」自认）；
② 候选组 role:/org: 引用认领时不展开（spec §5.5「groups 保留引用，认领时再展开」
   明文要求，is_member 端口早有实现但引擎不调）；
③ 无「我可认领的待办」查询维度（spec §12 tasks 过滤维度清单里的 candidate）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowork.api import (
    EngineConfig,
    FlowBuilder,
    ProcessEngine,
    StaticOrgDirectory,
    TaskNotAvailableError,
)
from flowork.definitions.validator import InvalidDefinition

ORG = {
    "users": {
        "u_alice": {"department": "d_fin", "roles": ["finance"]},
        "u_bob": {"department": "d_eng"},
        "u_admin": {},
    },
    "departments": {"d_fin": {"head": "u_alice"}, "d_eng": {"head": "u_bob"}},
    "delegations": [],
    "admins": ["u_admin"],
}


def _org_engine() -> ProcessEngine:
    return ProcessEngine(EngineConfig(org=StaticOrgDirectory.from_dict(ORG)))


def _candidate_flow(**spec: object) -> object:
    return (
        FlowBuilder("adhoc_candidate")
        .start()
        .user_task("pick", "候选任务", **spec)
        .end()
        .connect("start", "pick")
        .connect("pick", "end")
        .build()
    )


def _start(engine: ProcessEngine, start_user: str = "u_admin") -> str:
    return engine.runtime.start_process("adhoc_candidate", start_user=start_user)


# —— ① 配组织目录时候选制可用 ——


def test_candidate_task_usable_when_org_directory_configured() -> None:
    """配了目录，候选节点仍生成可认领任务（此前被无参与者兜底拦死，启动即挂起）。"""
    engine = _org_engine()
    engine.repository.deploy(_candidate_flow(candidate_users=["'u_bob'"]))
    iid = _start(engine)
    tasks = engine.task.list_tasks(instance_id=iid)
    assert [t.assignee for t in tasks] == [""], "候选任务不落名"
    claimed = engine.task.claim(tasks[0].id, "u_bob")
    engine.task.complete(claimed.id, actor="u_bob")
    assert engine.runtime.get_instance(iid).state == "completed"


def test_candidate_slots_win_over_to_admins_fallback() -> None:
    """候选优先于兜底：声明了候选槽就不把待办塞给管理员（to_admins 亦不越权）。"""
    engine = _org_engine()
    engine.repository.deploy(
        _candidate_flow(candidate_users=["'u_bob'"], no_participant_policy="to_admins")
    )
    iid = _start(engine)
    assert [t.assignee for t in engine.task.list_tasks(instance_id=iid)] == [""]


def test_assignee_still_wins_when_candidates_declared() -> None:
    """指派人优先于候选：取人位解析出人时照旧直派，候选槽不参与（与裸引擎路径一致）。"""
    engine = _org_engine()
    engine.repository.deploy(_candidate_flow(assignee="'u_alice'", candidate_users=["'u_bob'"]))
    iid = _start(engine)
    assert [t.assignee for t in engine.task.list_tasks(instance_id=iid)] == ["u_alice"]


def test_missed_assignee_expression_falls_back_to_candidates() -> None:
    """指派公式查无（null 传播）而声明了候选槽 → 落候选认领任务，不报「没有参与者」。

    与上一条成对：指派命中就直派，指派查无才让位给候选——兜底只留给两者皆空的节点。
    """
    engine = _org_engine()
    engine.repository.deploy(_candidate_flow(assignee="null", candidate_users=["'u_bob'"]))
    iid = _start(engine)
    assert [t.assignee for t in engine.task.list_tasks(instance_id=iid)] == [""]
    assert (
        engine.task.claim(engine.task.list_tasks(instance_id=iid)[0].id, "u_bob").assignee
        == "u_bob"
    )


# —— ② 组引用认领时展开（spec §5.5）——


def test_group_candidate_claim_expands_via_directory() -> None:
    """role: 组引用保留在候选名单，认领时问目录归属：组成员可认领，组外被拒。"""
    engine = _org_engine()
    engine.repository.deploy(_candidate_flow(candidate_groups=["'role:finance'"]))
    iid = _start(engine, start_user="u_bob")
    task = engine.task.list_tasks(instance_id=iid)[0]
    with pytest.raises(TaskNotAvailableError):
        engine.task.claim(task.id, "u_bob")
    assert engine.task.claim(task.id, "u_alice").assignee == "u_alice"


def test_org_group_candidate_claim_by_department_member() -> None:
    """org: 部门引用认领时展开（spec §5.4 部门树场景）。"""
    engine = _org_engine()
    engine.repository.deploy(_candidate_flow(candidate_groups=["'org:d_eng'"]))
    iid = _start(engine, start_user="u_alice")
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert engine.task.claim(task.id, "u_bob").assignee == "u_bob"


def test_bare_group_name_no_longer_expands_as_role() -> None:
    """拍板 6 方案 A（「角色就是角色，人名就是人名」）：candidate_groups 写裸名
    在设计期即被拦（candidate_groups.bare_reference）——旧「裸名视同 role」
    约定废止，组引用必须显式前缀。"""
    engine = _org_engine()
    with pytest.raises(InvalidDefinition, match=r"bare_reference"):
        engine.repository.deploy(_candidate_flow(candidate_groups=["'finance'"]))


def test_person_candidate_prefix_interchangeable() -> None:
    """user: 前缀与裸 id 互认（spec §5.4 前缀写法）：声明与认领两侧混用均可命中。"""
    engine = _org_engine()
    engine.repository.deploy(_candidate_flow(candidate_users=["'user:u_bob'"]))
    iid = _start(engine)
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert engine.task.claim(task.id, "u_bob").assignee == "u_bob"

    engine.repository.deploy(_candidate_flow(candidate_users=["'u_bob'"]))
    iid = _start(engine)
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert engine.task.claim(task.id, "user:u_bob").assignee == "user:u_bob"


def test_group_candidate_without_directory_hints_config() -> None:
    """无目录 + 组授权：拒绝并提示缺组织目录（不再是零信息的「不是候选人」）。"""
    engine = ProcessEngine()
    engine.repository.deploy(_candidate_flow(candidate_groups=["'role:finance'"]))
    iid = _start(engine)
    task = engine.task.list_tasks(instance_id=iid)[0]
    with pytest.raises(TaskNotAvailableError, match="组织目录"):
        engine.task.claim(task.id, "u_alice")


def test_claim_keeps_candidate_links_and_blocks_second_candidate() -> None:
    """认领保留候选关联（谁本来有资格认领是审计信息）；他人再认领得「已被认领」。"""
    engine = _org_engine()
    engine.repository.deploy(_candidate_flow(candidate_users=["'u_bob'", "'u_alice'"]))
    iid = _start(engine)
    task = engine.task.list_tasks(instance_id=iid)[0]
    engine.task.claim(task.id, "u_bob")
    with pytest.raises(TaskNotAvailableError, match="已被"):
        engine.task.claim(task.id, "u_alice")


# —— ③ 候选查询维度（「我可认领的待办」）——


def test_list_tasks_candidate_dimension_lists_claimable() -> None:
    """candidate 维度＝我可认领的待办；认领后关联保留（本人可 unclaim 回池）。"""
    engine = _org_engine()
    engine.repository.deploy(_candidate_flow(candidate_users=["'u_bob'"]))
    iid = _start(engine)
    task = engine.task.list_tasks(instance_id=iid)[0]

    assert [t.id for t in engine.task.list_tasks(candidate="u_bob")] == [task.id]
    assert engine.task.list_tasks(candidate="u_alice") == []

    engine.task.claim(task.id, "u_bob")
    assert [t.id for t in engine.task.list_tasks(candidate="u_bob", assignee="u_bob")] == [task.id]
    # 「仍在候选池」＝未被认领：与 assignee="" 组合即该口径
    assert engine.task.list_tasks(candidate="u_bob", assignee="") == []


def test_list_tasks_candidate_dimension_expands_groups() -> None:
    """候选维度同样走组展开（认领时展开的同一判定，查询侧预演）。"""
    engine = _org_engine()
    engine.repository.deploy(_candidate_flow(candidate_groups=["'role:finance'"]))
    iid = _start(engine, start_user="u_bob")
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert [t.id for t in engine.task.list_tasks(candidate="u_alice")] == [task.id]
    assert engine.task.list_tasks(candidate="u_bob") == []


def test_group_reference_stays_live_after_join(tmp_path: Path) -> None:
    """决定性：组引用保留不展开（spec §5.5）——建任务后才入组的人也能认领。

    这条钉住「引用保留、认领时展开」与「建任务时展开定格」的分野：后者在这条上
    必然失败（快照语义下 u_late 永远不是候选人）。持久化目录支持事后入组。
    """
    from flowork.directory.store import DirectoryStore
    from flowork.persistence.db import create_db_engine, file_url, session_factory

    store = DirectoryStore(session_factory(create_db_engine(file_url(tmp_path / "org.db"))))
    store.create_org_unit("d_fin", name="财务部")
    store.create_user("u_early", org_unit_code="d_fin")
    store.create_user("u_late", org_unit_code="d_fin")
    store.create_role("finance", name="财务审批人")
    store.assign_role("u_early", "finance")

    engine = ProcessEngine(EngineConfig(org=store))
    engine.repository.deploy(_candidate_flow(candidate_groups=["'role:finance'"]))
    iid = _start(engine, start_user="u_early")
    task = engine.task.list_tasks(instance_id=iid)[0]

    with pytest.raises(TaskNotAvailableError):
        engine.task.claim(task.id, "u_late")  # 尚未入组

    store.assign_role("u_late", "finance")  # 入组（读缓存随写失效）

    assert [t.id for t in engine.task.list_tasks(candidate="u_late")] == [task.id]
    assert engine.task.claim(task.id, "u_late").assignee == "u_late"


# —— unclaim 守卫（内核 code-review 2026-10 P0-4）——


def test_unclaim_rejected_for_directly_assigned_task() -> None:
    """直派任务不可取消认领：无候选池可回，置空后 claim/complete 双拒——
    修复前任务陷入「无人能办、无故障单」的永久卡死。"""
    engine = _org_engine()
    engine.repository.deploy(_candidate_flow(assignee="'u_bob'"))
    iid = _start(engine)
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert task.assignee == "u_bob"

    with pytest.raises(TaskNotAvailableError, match="直接指派"):
        engine.task.unclaim(task.id, "u_bob")
    # 任务未被破坏：仍可正常办结
    engine.task.complete(task.id, actor="u_bob")
    assert engine.runtime.get_instance(iid).state == "completed"


def test_unclaim_returns_candidate_task_to_pool() -> None:
    """候选任务的取消认领语义保持：回池（assignee 置空、候选关联保留），可再认领。"""
    engine = _org_engine()
    engine.repository.deploy(_candidate_flow(candidate_users=["'u_bob'"]))
    iid = _start(engine)
    task = engine.task.list_tasks(instance_id=iid)[0]
    claimed = engine.task.claim(task.id, "u_bob")

    released = engine.task.unclaim(claimed.id, "u_bob")
    assert released.assignee == ""
    again = engine.task.claim(claimed.id, "u_bob")  # 候选关联保留——回池后仍可认领
    assert again.assignee == "u_bob"
    engine.task.complete(again.id, actor="u_bob")
    assert engine.runtime.get_instance(iid).state == "completed"


def test_colliding_role_name_no_longer_expands_claim_pool() -> None:
    """回归（拍板 6 方案 A 核心场景）：人名单裸名撞角色名——认领面不再静默
    扩大。目录里存在与用户同名的角色（wangwei，成员 xiaoli）：修复前
    candidate_users=["'wangwei'"] 会把 xiaoli 也放进认领池（裸名溢出为角色
    名）；现在裸名只按人解释，只有 wangwei 本人可认领。"""
    org = {
        "users": {
            "u_wangwei": {"roles": []},
            "u_xiaoli": {"roles": ["wangwei"]},  # 角色 wangwei 的成员
            "u_admin": {},
        },
        "departments": {},
        "delegations": [],
        "admins": ["u_admin"],
    }
    engine = ProcessEngine(EngineConfig(org=StaticOrgDirectory.from_dict(org)))
    engine.repository.deploy(_candidate_flow(candidate_users=["'u_wangwei'"]))
    iid = _start(engine)
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert engine.task.list_tasks(candidate="u_xiaoli") == [], "撞名角色成员不在认领池"
    with pytest.raises(TaskNotAvailableError):
        engine.task.claim(task.id, "u_xiaoli")
    claimed = engine.task.claim(task.id, "u_wangwei")
    assert claimed.assignee == "u_wangwei"
