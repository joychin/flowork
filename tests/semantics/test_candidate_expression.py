"""候选槽表达式（B 批，spec §5.5/§16）：candidate_users/candidate_groups 是 CEL 公式。

来源 2026-10-09 候选槽对齐批（candidate-expression-design.md，零兼容）——规格三处
（§2.2 字段表「取人，支持表达式」/ §5.4 主体格式 / §5.5 槽位表「mixed 自动拆槽」）
写明候选字段为表达式；对齐前：运行时从不求值（公式原文入库、永无人能认领）、
设计时校验不收录、部署校验半收录且对组引用必误报。

本文件钉四组语义：
- 运行时：求值（与取人位同一求值面）/ 人定格·组保留 / 空结果兜底 / 求值错=技术故障 /
  规模护栏；
- 设计时：候选位收录（语法/未声明变量）、保留词汇位点归一（prev_task/node() 可用、
  task.* 不可用）、returns 白名单（候选槽多接受 groups/mixed）；
- 部署期 L4：组引用跳过「是不是人」的存在性检查（只查会被当成人解释的字面量）；
- 迁移语义：裸名（无引号）从此是公式引用而非人名——响亮失败，不再静默。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from flowork.api import (
    EngineConfig,
    ExpressionFunction,
    FlowBuilder,
    FunctionSignature,
    ProcessEngine,
    StaticOrgDirectory,
)
from flowork.definitions.definition import ProcessVariable
from flowork.definitions.validator import IssueLevel, validate_definition
from flowork.engine.kernel import (
    EngineExecutionError,
    NoParticipantError,
    ParticipantLimitError,
)
from flowork.errors import TaskNotAvailableError
from flowork.persistence.orm import IdentityLinkRow, TaskRow

ORG = {
    "users": {
        "u_fin1": {"roles": ["finance"]},
        "u_fin2": {"roles": ["finance"]},
        "u_eng": {"department": "d_eng"},
        "u_bob": {},
    },
    "departments": {"d_eng": {}},
    "delegations": [],
    "admins": ["u_admin"],
}


def _candidate_flow(**spec: object) -> object:
    return (
        FlowBuilder("cand_expr")
        .start()
        .user_task("pick", "候选任务", **spec)
        .end()
        .connect("start", "pick")
        .connect("pick", "end")
        .build()
    )


def _links_of(engine: ProcessEngine, instance_id: str) -> list[str]:
    with engine.session_factory() as session:
        task_id = session.scalars(
            sa.select(TaskRow).where(TaskRow.instance_id == instance_id)
        ).first()
        return [
            row.user_id
            for row in session.scalars(
                sa.select(IdentityLinkRow).where(IdentityLinkRow.task_id == task_id.id)
            )
        ]


# —— 宿主注册面：returns=groups / mixed 的函数（证明候选槽接受组与混合集）——


class _GroupFunctions:
    """宿主词汇：finance_path 返回组引用、review_board 返回混合集。"""

    def functions(self):  # type: ignore[no-untyped-def]
        return {
            "finance_path": ExpressionFunction(
                FunctionSignature((), "groups", label="财务审批组（引用）"),
                lambda: ["role:finance"],
            ),
            "review_board": ExpressionFunction(
                FunctionSignature((), "mixed", label="评审团（人+组）"),
                lambda: ["u_bob", "role:finance"],
            ),
            "nobody": ExpressionFunction(
                FunctionSignature((), "users", label="空名单"),
                lambda: [],
            ),
            "three_users": ExpressionFunction(
                FunctionSignature((), "users", label="三人名单"),
                lambda: ["u_fin1", "u_fin2", "u_eng"],
            ),
        }


def _engine(**config: object) -> ProcessEngine:
    return ProcessEngine(
        EngineConfig(org=StaticOrgDirectory.from_dict(ORG), **config)  # type: ignore[arg-type]
    )


# —— ① 运行时：求值、定格与保留 ——


def test_function_candidates_are_people_frozen_at_entry() -> None:
    """取人函数候选：求值出的人**定格**进候选名单——同组后来者不能认领该待办。

    与组引用（保留、认领时展开）成对：人是快照，组是活的（spec §5.5 求值时机表）。
    """
    engine = ProcessEngine(
        EngineConfig(
            org=StaticOrgDirectory.from_dict(ORG),
            expression_functions=_GroupFunctions(),
        )
    )
    engine.repository.deploy(_candidate_flow(candidate_users=["three_users()"]))
    iid = engine.runtime.start_process("cand_expr", start_user="u_bob")
    assert sorted(_links_of(engine, iid)) == ["u_eng", "u_fin1", "u_fin2"]
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert engine.task.claim(task.id, "u_fin1").assignee == "u_fin1"


def test_function_returning_group_references_kept_as_is() -> None:
    """returns=groups 的函数：组引用**保留**落名单（不展开），认领时按目录归属。"""
    engine = ProcessEngine(
        EngineConfig(
            org=StaticOrgDirectory.from_dict(ORG),
            expression_functions=_GroupFunctions(),
        )
    )
    engine.repository.deploy(_candidate_flow(candidate_groups=["finance_path()"]))
    iid = engine.runtime.start_process("cand_expr", start_user="u_bob")
    assert _links_of(engine, iid) == ["role:finance"], "组引用按值保留，不展开"
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert engine.task.claim(task.id, "u_fin2").assignee == "u_fin2"  # 组员认领
    engine.task.unclaim(task.id, "u_fin2")
    with pytest.raises(TaskNotAvailableError):
        engine.task.claim(task.id, "u_bob")  # 非组员：候选命中两式皆不中


def test_mixed_results_stored_in_one_pool() -> None:
    """returns=mixed：人与组引用混入同一候选名单（运行期不拆槽——判定按值前缀）。"""
    engine = ProcessEngine(
        EngineConfig(
            org=StaticOrgDirectory.from_dict(ORG),
            expression_functions=_GroupFunctions(),
        )
    )
    engine.repository.deploy(_candidate_flow(candidate_users=["review_board()"]))
    iid = engine.runtime.start_process("cand_expr", start_user="u_bob")
    assert sorted(_links_of(engine, iid)) == ["role:finance", "u_bob"]
    task = engine.task.list_tasks(instance_id=iid)[0]
    engine.task.claim(task.id, "u_bob")  # 人经精确匹配
    engine.task.unclaim(task.id, "u_bob")
    engine.task.claim(task.id, "u_fin1")  # 组员经目录归属


def test_person_name_in_groups_slot_rejected() -> None:
    """拍板 6 方案 A（「角色就是角色，人名就是人名」）：组槽位写人名＝设计期
    ERROR——槽位定解释，不再有「精确匹配兜底」的宽容解释；人名请写
    candidate_users。旧测试钉的「字段默认让位于真值」语义随拍板废止。"""
    import pytest

    from flowork.definitions.validator import InvalidDefinition

    engine = _engine()
    with pytest.raises(InvalidDefinition, match=r"bare_reference"):
        engine.repository.deploy(_candidate_flow(candidate_groups=["'u_bob'"]))
    # 正路：人名写人槽位，照常精确命中
    engine.repository.deploy(_candidate_flow(candidate_users=["'u_bob'"]))
    iid = engine.runtime.start_process("cand_expr", start_user="u_bob")
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert engine.task.claim(task.id, "u_bob").assignee == "u_bob"


def test_candidate_expression_reads_variables() -> None:
    """候选公式吃流程变量（与取人位同一装配）：变量值即候选人。"""
    engine = _engine()
    flow = (
        FlowBuilder("cand_var")
        .start()
        .user_task("pick", candidate_users=["pool"])
        .end()
        .connect("start", "pick")
        .connect("pick", "end")
        .build()
        .model_copy(update={"variables": [ProcessVariable(name="pool", type="string")]})
    )
    engine.repository.deploy(flow)
    iid = engine.runtime.start_process("cand_var", start_user="u_bob", variables={"pool": "u_eng"})
    assert _links_of(engine, iid) == ["u_eng"]


# —— ② 空结果与错误二分 ——


def test_empty_evaluation_raises_no_participant() -> None:
    """候选声明了但求值为空 → 走 no_participant_policy（缺省内联拒绝）。"""
    engine = ProcessEngine(
        EngineConfig(
            org=StaticOrgDirectory.from_dict(ORG),
            expression_functions=_GroupFunctions(),
        )
    )
    engine.repository.deploy(_candidate_flow(candidate_users=["nobody()"]))
    with pytest.raises(NoParticipantError):
        engine.runtime.start_process("cand_expr", start_user="u_bob")


def test_empty_evaluation_to_admins_assigns_administrators() -> None:
    """候选求值为空 + policy=to_admins → 管理员直接拿待办（不建候选任务）。"""
    engine = ProcessEngine(
        EngineConfig(
            org=StaticOrgDirectory.from_dict(ORG),
            expression_functions=_GroupFunctions(),
        )
    )
    engine.repository.deploy(
        _candidate_flow(candidate_users=["nobody()"], no_participant_policy="to_admins")
    )
    iid = engine.runtime.start_process("cand_expr", start_user="u_bob")
    assert [t.assignee for t in engine.task.list_tasks(instance_id=iid)] == ["u_admin"]


def test_evaluation_error_is_technical_fault() -> None:
    """求值错（未注册函数）＝技术故障：挂起 + 故障单（错误二分，与指派位一致）。

    两段式流程：候选节点在第二站——首站办结推进后求值失败，实例已落库，
    「挂起+故障单」语义可见（发起即失败则整笔回滚、实例不落库，与指派位同款）。
    """
    engine = _engine()  # 无 expression_functions：users_with_role 未注册
    flow = (
        FlowBuilder("cand_fault")
        .start()
        .user_task("first", assignee="'u_bob'")
        .user_task("pick", candidate_users=["users_with_role('finance')"])
        .end()
        .connect("start", "first")
        .connect("first", "pick")
        .connect("pick", "end")
        .build()
    )
    engine.repository.deploy(flow)
    iid = engine.runtime.start_process("cand_fault", start_user="u_bob")
    first = engine.task.list_tasks(instance_id=iid)[0]
    with pytest.raises(EngineExecutionError):
        engine.task.complete(first.id, actor="u_bob")
    assert engine.runtime.get_instance(iid).state == "suspended"
    incidents = engine.runtime.list_incidents(instance_id=iid)
    assert incidents and incidents[0]["state"] == "open"
    assert "候选公式求值失败" in incidents[0]["message"]


def test_candidate_pool_respects_participant_limit() -> None:
    """候选名单同守规模护栏：超出 max_participants 拒绝，绝不截断。"""
    engine = ProcessEngine(
        EngineConfig(
            org=StaticOrgDirectory.from_dict(ORG),
            expression_functions=_GroupFunctions(),
            max_participants=2,
        )
    )
    engine.repository.deploy(_candidate_flow(candidate_users=["three_users()"]))
    with pytest.raises(ParticipantLimitError):
        engine.runtime.start_process("cand_expr", start_user="u_bob")


# —— ③ 设计时校验：候选位收录与位点归一 ——


def _validate(definition: object, **kwargs: object) -> list[tuple[str, IssueLevel]]:
    issues = validate_definition(
        definition,  # type: ignore[arg-type]
        ProcessEngine().config.node_registry,
        **kwargs,  # type: ignore[arg-type]
    )
    return [(i.rule, i.level) for i in issues]


def test_validator_flags_bare_group_reference_as_syntax_error() -> None:
    """裸组引用 role:legal（无引号）＝ CEL 语法错——设计期 ERROR（迁移点：加引号）。"""
    issues = _validate(_candidate_flow(candidate_groups=["role:legal"]))
    assert ("expression.formula.invalid", IssueLevel.ERROR) in issues


def test_validator_flags_undeclared_variable_in_candidates() -> None:
    """裸人名按变量引用检查：有声明变量时未声明即 ERROR（与指派位同款）。"""
    flow = (
        FlowBuilder("cand_undeclared")
        .start()
        .user_task("pick", candidate_users=["u_bob"])
        .end()
        .connect("start", "pick")
        .connect("pick", "end")
        .build()
    )
    flow.variables = [ProcessVariable(name="amount", type="number", sample=1)]
    issues = _validate(flow)
    assert ("expression.variable.unknown", IssueLevel.ERROR) in issues


def test_validator_reserved_vocabulary_normalizes_candidate_kind() -> None:
    """位点归一：prev_task / node() 在候选位可用（取人位词汇），task.* 不可用。"""
    flow = (
        FlowBuilder("cand_vocab")
        .start()
        .user_task("pick", candidate_users=["'u_bob'"])
        .user_task("bad", candidate_users=["task.assignee"])
        .user_task("ok", candidate_users=['node("pick").assignee'])
        .end()
        .connect("start", "pick")
        .connect("pick", "ok")
        .connect("ok", "bad")
        .connect("bad", "end")
        .build()
    )
    flow.variables = [ProcessVariable(name="amount", type="number", sample=1)]
    rules = {rule for rule, _level in _validate(flow)}
    assert "expression.task_unavailable" in rules, "task.* 在候选位应报不可用"
    assert not any("node" in r for r in rules), "node() 在候选位可用（归一为取人位）"


def test_candidate_returns_whitelist_accepts_groups_and_mixed() -> None:
    """returns 对位：groups/mixed 在候选位放行、在指派位（assignees）报错。

    引用检查须声明了流程变量才启用（与指派位同款门槛）——两翼都带一个声明变量。
    """
    host = _GroupFunctions()
    ok_flow = _candidate_flow(candidate_groups=["finance_path()", "review_board()"])
    ok_flow.variables = [ProcessVariable(name="amount", type="number", sample=1)]
    assert not [i for i in _validate(ok_flow, expression_functions=host) if "returns" in i[0]]
    bad_flow = (
        FlowBuilder("cand_ret_bad")
        .start()
        .user_task("pick", assignees=["finance_path()"])
        .end()
        .connect("start", "pick")
        .connect("pick", "end")
        .build()
    )
    bad_flow.variables = [ProcessVariable(name="amount", type="number", sample=1)]
    rules = {rule for rule, _level in _validate(bad_flow, expression_functions=host)}
    assert "expression.function.returns" in rules, "指派位不接受 groups"


# —— ④ 部署期 L4：组引用跳过存在性检查 ——


class _KnownOrg:
    """最小目录钩子：只认 u_bob。"""

    def __init__(self) -> None:
        self.admins: list[str] = []

    def subject_exists(self, subject: str) -> bool:
        return subject == "u_bob"

    def is_member(self, user: str, group: str) -> bool:
        return False


def test_l4_skips_group_references_but_checks_people() -> None:
    """组引用（显式前缀或候选组字段）不问「是不是人」；人名字面量照查。"""
    from flowork.definitions.validator_deploy import check_deploy_references

    flow = _candidate_flow(
        candidate_groups=["'role:ghost_group'"],
        candidate_users=["'role:ghost_group'", "'u_ghost'", "'u_bob'"],
    )
    issues = check_deploy_references(
        flow,  # type: ignore[arg-type]
        session=None,  # type: ignore[arg-type]
        handlers=(),
        subject_exists=_KnownOrg().subject_exists,
    )
    subject_issues = [i for i in issues if i.rule == "participant.subject_unknown"]
    assert [i.message.split("字面量 ")[1].split(" ")[0] for i in subject_issues] == ["'u_ghost'"]
