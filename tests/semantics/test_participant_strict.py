"""取人语义收口批（spec v2.3 第十八轮）测试。

四处收口的决定性判例（来源：2026-10-10 用户裁定——单人节点配多人应报错、
组引用要么报错要么展开【行业调研后定：设计期报错】、max_participants 不写死、
名单去重要好好测）：

- 单人节点（未声明 multi_instance）解析出多名处理人＝运行期错误，拒绝宽松并行；
- 人员位（assignee/assignees/recipients/超时目标）出现组引用字面量＝设计期
  ERROR——组引用只在候选槽有意义（spec §5.5「两个槽不可拍平」）；
- ``max_participants`` 缺省 None＝不设防，显式配置才启用；
- 取人名单跨表达式去重：一人一任务、会签一票。
"""

from __future__ import annotations

import pytest

from flowork import contrib
from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition
from flowork.definitions.node import MultiInstance, TaskDeadline
from flowork.definitions.validator import IssueLevel, validate_definition
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.engine.kernel import ParticipantCountMismatchError, ParticipantLimitError
from flowork.participants.org_directory import OrgDirectory
from flowork.runtime.registry import create_registry

ORG_DATA = {
    "users": {
        "u_fin1": {"department": "d_fin"},
        "u_fin2": {"department": "d_fin"},
        "u_bob": {"manager": "u_ceo", "department": "d_eng"},
    },
    "admins": ["u_bob"],
}


def error_rules(definition: ProcessDefinition) -> set[str]:
    return {
        issue.rule
        for issue in validate_definition(definition, create_registry())
        if issue.level == IssueLevel.ERROR
    }


# —— ① 单人节点多人＝运行期错误 ——


def test_single_node_multiple_people_rejected_at_start():
    """首节点单人配多人：start 整笔回滚，实例不落地（既有命令边界语义）。"""
    engine = ProcessEngine()
    flow = (
        FlowBuilder("strict1")
        .start()
        .user_task("a", assignees=["'u1'", "'u2'"])
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    engine.repository.deploy(flow.build())
    with pytest.raises(ParticipantCountMismatchError, match="未声明会签"):
        engine.runtime.start_process("strict1", start_user="u_s")
    assert engine.history.instances() == []


def test_single_node_multiple_people_mid_flow_suspends_with_incident():
    """中段单人配多人：技术故障语义＝挂起+故障单（可见、可修、可 retry）。"""
    engine = ProcessEngine()
    flow = (
        FlowBuilder("strict2")
        .start()
        .user_task("a", assignee="'u1'")
        .user_task("b", assignees=["'u1'", "'u2'"])
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("strict2", start_user="u_s")
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    with pytest.raises(ParticipantCountMismatchError):
        engine.task.complete(task_a.id, actor="u1")
    assert engine.runtime.get_instance(instance_id).state == "suspended"
    incidents = engine.runtime.list_incidents(instance_id=instance_id)
    assert incidents and incidents[0]["state"] == "open"
    assert "未声明会签" in incidents[0]["message"]


def test_declared_parallel_multi_instance_dispatches_all():
    """对照：显式声明并行会签后多人正常下发——报错只打「未声明」的。"""
    engine = ProcessEngine()
    flow = (
        FlowBuilder("strict3")
        .start()
        .user_task(
            "a",
            assignees=["'u1'", "'u2'"],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("strict3", start_user="u_s")
    assert sorted(t.assignee for t in engine.task.list_tasks(instance_id=instance_id)) == [
        "u1",
        "u2",
    ]


def test_skipped_participants_do_not_count_as_multiple():
    """auto_pass 命中后 todo 只剩一人：单人语义成立，不误伤。"""
    engine = ProcessEngine()
    flow = (
        FlowBuilder("strict4")
        .start()
        .user_task("a", assignees=["'u_s'", "'u2'"], auto_pass_originator=True)
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("strict4", start_user="u_s")
    tasks = engine.task.list_tasks(instance_id=instance_id)
    assert [t.assignee for t in tasks] == ["u2"]


# —— ② 人员位组引用＝设计期 ERROR（组引用只在候选槽，spec §5.5）——


def test_group_reference_in_assignees_position_is_error():
    flow = (
        FlowBuilder("gv1")
        .start()
        .user_task("a", assignees=["'role:finance'"])
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    assert "participant.group_in_subjects" in error_rules(flow.build())


def test_group_reference_flagged_across_people_positions():
    """单人取人位 / 传阅接收人 / 超时改派目标：三个人员位同拦。"""
    flow = (
        FlowBuilder("gv2")
        .start()
        .user_task(
            "a",
            assignee="'org:d_fin'",
            deadlines=[
                TaskDeadline(after_minutes=15, action="reassign", reassign_to="'role:boss'")
            ],
        )
        .cc_task("n", recipients=["'role:auditor'"])
        .end()
        .connect("start", "a")
        .connect("a", "n")
        .connect("n", "end")
    )
    issues = [
        issue
        for issue in validate_definition(flow.build(), create_registry())
        if issue.rule == "participant.group_in_subjects"
    ]
    assert {issue.node for issue in issues} == {"a", "n"}


def test_group_reference_in_candidate_slot_stays_legal():
    """候选槽写同一引用不报错——那是组引用的唯一归属。"""
    flow = (
        FlowBuilder("gv3")
        .start()
        .user_task("a", candidate_groups=["'role:finance'"])
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    assert "participant.group_in_subjects" not in error_rules(flow.build())


# 来源：2026-10-24 subagent 对打批——manager_of('org:部门') 曾被本规则拦死，
# 与词汇契约（吃 org: 返回部门负责人）和运行时实现三方矛盾。修法＝参数位放行。


def test_manager_of_org_argument_passes_and_runs():
    """manager_of 参数位组引用＝部门负责人语义：设计期放行，运行时落到部门 head。

    回归来源：2026-10-24 取人对打 FINDINGS P1——「找部门负责人审批」在人员位
    曾无合法写法（词汇 docstring/运行时支持 vs 设计期 ERROR 拦死）。
    """
    org = OrgDirectory.from_dict(
        {
            "users": {
                "u_emp": {"department": "d_eng"},
                "u_head": {"department": "d_eng"},
            },
            "departments": {"d_eng": {"head": "u_head"}},
        }
    )
    engine = ProcessEngine(
        EngineConfig(
            org=org,
            expression_functions=contrib.cel_vocabulary(org),
            node_registry=create_registry(),
        )
    )
    flow = (
        FlowBuilder("gvm1")
        .start()
        .user_task("a", assignee="manager_of('org:d_eng')")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    assert "participant.group_in_subjects" not in error_rules(flow.build())
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("gvm1", start_user="u_emp")
    rows = engine.task.list_tasks(instance_id=instance_id)
    assert [r.assignee for r in rows] == ["u_head"]


def test_expansion_vocab_group_prefix_argument_still_flagged():
    """展开型词汇参数带前缀＝写错（users_with_role 拼 role: 前缀、users_in_org 拼 org:），
    防呆保留：manager_of 放行不外溢到其他词汇。"""
    flow = (
        FlowBuilder("gv5")
        .start()
        .user_task("a", assignee="users_with_role('role:finance')")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    flow2 = (
        FlowBuilder("gv6")
        .start()
        .user_task(
            "a",
            assignees=["users_in_org('org:d_eng')"],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    assert "participant.group_in_subjects" in error_rules(flow.build())
    assert "participant.group_in_subjects" in error_rules(flow2.build())


def test_user_prefix_and_bare_id_stay_legal():
    """user: 前缀互认（spec §5.4）与裸 id 都不是组引用，不拦。"""
    flow = (
        FlowBuilder("gv4")
        .start()
        .user_task("a", assignee="'user:u1'", assignees=["'u_plain'"])
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    assert "participant.group_in_subjects" not in error_rules(flow.build())


# —— ③ max_participants：缺省不设防，显式配置才启用 ——


def test_max_participants_defaults_to_none():
    assert EngineConfig().max_participants is None


def test_explicit_limit_still_guards_at_start():
    """显式配置后护栏照常生效（首节点触发＝整笔回滚）。"""
    engine = ProcessEngine(EngineConfig(max_participants=1))
    flow = (
        FlowBuilder("strict5")
        .start()
        .user_task(
            "a",
            assignees=["'u1'", "'u2'"],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    engine.repository.deploy(flow.build())
    with pytest.raises(ParticipantLimitError):
        engine.runtime.start_process("strict5", start_user="u_s")


# —— ④ 名单去重：跨表达式交集一人一任务一票 ——


def test_dedupe_preserves_first_seen_order():
    from flowork.participants.resolver import _dedupe

    assert _dedupe(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]
    assert _dedupe([]) == []


def test_overlapping_expressions_yield_one_task_one_vote():
    """initiator 与字面名单交集：u1 只建一条待办、会签只算一票。"""
    engine = ProcessEngine()
    flow = (
        FlowBuilder("strict6")
        .start()
        .user_task(
            "a",
            assignees=["initiator", "['u1', 'u2']"],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("strict6", start_user="u1")
    tasks = {t.assignee: t for t in engine.task.list_tasks(instance_id=instance_id)}
    assert set(tasks) == {"u1", "u2"}
    engine.task.complete(tasks["u1"].id, actor="u1")
    result = engine.task.complete(tasks["u2"].id, actor="u2")
    assert result.instance_state == "completed"
    token = next(t for t in engine.history.tokens(instance_id) if t.activity_code == "a")
    assert token.item_count == 2


# —— ⑤ users_in_org：取人位按部门展开的唯一写法 ——


def test_users_in_org_expands_department_members():
    org = OrgDirectory.from_dict(ORG_DATA)
    engine = ProcessEngine(EngineConfig(org=org, expression_functions=contrib.cel_vocabulary(org)))
    flow = (
        FlowBuilder("strict7")
        .start()
        .user_task(
            "a",
            assignee="users_in_org('d_fin')",
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    engine.repository.deploy(flow.build())
    instance_id = engine.runtime.start_process("strict7", start_user="u_s")
    assert sorted(t.assignee for t in engine.task.list_tasks(instance_id=instance_id)) == [
        "u_fin1",
        "u_fin2",
    ]
