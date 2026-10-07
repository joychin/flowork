"""推进门（advance_policy）语义测试：any 任一到达 / all 等全部到齐 / 吸收 / 驳回绕门 / 校验。

对位 对标产品：preCondition(ALL/ANY) + handleActiviteActivity（或汇聚取消兄弟）。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.builtin import RouteMode
from flowork.definitions.flow import FlowCondition
from flowork.engine.engine import ProcessEngine


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def cond(left: str, op: str, right: object) -> FlowCondition:
    return FlowCondition(groups=[[{"left": left, "operator": op, "right": right}]])


def publish(engine: ProcessEngine, definition) -> None:
    engine.repository.save_definition(definition)
    engine.repository.publish(definition.code)


def active_of(engine: ProcessEngine, instance_id: str) -> set[str]:
    return {t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)}


def test_all_waits_for_every_source(engine: ProcessEngine):
    """all：先到的分支被推进门拦住（不建令牌），全部到齐才激活一次。"""
    definition = (
        FlowBuilder("adv_all")
        .start()
        .user_task("left", assignee="u_l")
        .user_task("right", assignee="u_r")
        .user_task("merged", assignee="u_m", advance_policy="all")
        .end()
        .connect("start", "left")
        .connect("start", "right")
        .connect("left", "merged")
        .connect("right", "merged")
        .connect("merged", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("adv_all", start_user="u_user")
    assert active_of(engine, instance_id) == {"left", "right"}

    left_task = engine.task.list_tasks(instance_id=instance_id, assignee="u_l")[0]
    engine.task.complete(left_task.id, actor="u_l")
    assert active_of(engine, instance_id) == {"right"}  # 未到齐，merged 未激活
    assert [t for t in engine.history.tokens(instance_id) if t.activity_code == "merged"] == []

    right_task = engine.task.list_tasks(instance_id=instance_id, assignee="u_r")[0]
    result = engine.task.complete(right_task.id, actor="u_r")
    assert [t.activity_code for t in result.next_tasks] == ["merged"]
    merged_tokens = [t for t in engine.history.tokens(instance_id) if t.activity_code == "merged"]
    assert len(merged_tokens) == 1  # 只激活一次（无重复到达令牌）


def test_any_cancels_sibling_and_activates_once(engine: ProcessEngine):
    """any（竞速）：任一到达即开始，并取消仍在办的来源分支；只激活一次。"""
    definition = (
        FlowBuilder("adv_absorb")
        .start()
        .user_task("left", assignee="u_l")
        .user_task("right", assignee="u_r")
        .user_task("merged", assignee="u_m", advance_policy="any")
        .end()
        .connect("start", "left")
        .connect("start", "right")
        .connect("left", "merged")
        .connect("right", "merged")
        .connect("merged", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("adv_absorb", start_user="u_user")

    left_task = engine.task.list_tasks(instance_id=instance_id, assignee="u_l")[0]
    engine.task.complete(left_task.id, actor="u_l")
    # 任一到达即开始；仍在办的 right 兄弟分支被取消（竞速收尾，防迟到重复到达）
    assert active_of(engine, instance_id) == {"merged"}
    right_token = next(t for t in engine.history.tokens(instance_id) if t.activity_code == "right")
    assert right_token.state == "cancelled"
    assert engine.task.list_tasks(instance_id=instance_id, assignee="u_r") == []
    merged_tokens = [t for t in engine.history.tokens(instance_id) if t.activity_code == "merged"]
    assert len(merged_tokens) == 1


def test_default_policy_is_any(engine: ProcessEngine):
    """缺省 advance_policy=None 按 any 执行：先到先办，后到吸收。"""
    definition = (
        FlowBuilder("adv_default")
        .start()
        .user_task("left", assignee="u_l")
        .user_task("right", assignee="u_r")
        .user_task("merged", assignee="u_m")
        .end()
        .connect("start", "left")
        .connect("start", "right")
        .connect("left", "merged")
        .connect("right", "merged")
        .connect("merged", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("adv_default", start_user="u_user")
    left_task = engine.task.list_tasks(instance_id=instance_id, assignee="u_l")[0]
    engine.task.complete(left_task.id, actor="u_l")
    # 缺省按 any：left 一到 merged 即开始（若误按 all 则 merged 不会出现）
    assert "merged" in active_of(engine, instance_id)


def test_reject_bypasses_advance_gate(engine: ProcessEngine):
    """驳回绕过推进门：all 节点在另一来源未完成时也能被立即驳回到并重建待办。"""
    definition = (
        FlowBuilder("adv_reject")
        .start()
        .user_task("a", assignee="u_a")
        .user_task("b", assignee="u_b", reject_target="merged")
        .user_task("merged", assignee="u_m", advance_policy="all")
        .end()
        .connect("start", "a")
        .connect("start", "b")
        .connect("a", "merged")
        .connect("b", "merged")
        .connect("merged", "end")
        .build()
    )
    publish(engine, definition)
    instance_id = engine.runtime.start_process("adv_reject", start_user="u_user")
    assert active_of(engine, instance_id) == {"a", "b"}

    # b 驳回到 merged：此时 a 未完成（all 门本应拦截），但驳回路径不经推进门
    b_task = engine.task.list_tasks(instance_id=instance_id, assignee="u_b")[0]
    result = engine.task.reject(b_task.id, actor="u_b", comment="退回合并点")
    assert [t.activity_code for t in result.next_tasks] == ["merged"]

    # merged 立即重建待办（绕门证据）；a 分支按既有驳回语义被取消
    assert active_of(engine, instance_id) == {"merged"}
    a_token = next(t for t in engine.history.tokens(instance_id) if t.activity_code == "a")
    assert a_token.state == "cancelled"
    assert len(engine.task.list_tasks(instance_id=instance_id, assignee="u_m")) == 1


def test_all_match_fan_out_from_branch(engine: ProcessEngine):
    """分支(命中的都走)双条件扇出 = 同时进行；未命中走默认边。"""
    definition = (
        FlowBuilder("adv_fan")
        .start()
        .branch("route", route_mode=RouteMode.ALL_MATCH)
        .user_task("a", assignee="u_a")
        .user_task("b", assignee="u_b")
        .user_task("fallback", assignee="u_f")
        .end(advance_policy="any")
        .connect("start", "route")
        .flow("route", "a", condition=cond("x", "ge", 1))
        .flow("route", "b", condition=cond("x", "ge", 1))
        .flow("route", "fallback", default=True)
        .connect("a", "end")
        .connect("b", "end")
        .connect("fallback", "end")
        .build()
    )
    publish(engine, definition)

    both = engine.runtime.start_process("adv_fan", start_user="u_user", variables={"x": 1})
    assert active_of(engine, both) == {"a", "b"}

    neither = engine.runtime.start_process("adv_fan", start_user="u_user", variables={"x": 0})
    assert active_of(engine, neither) == {"fallback"}


def test_first_match_takes_first_true_in_order(engine: ProcessEngine):
    """命中即走：按声明顺序取第一条为真（顺序敏感）。"""
    definition = (
        FlowBuilder("adv_fm")
        .start()
        .branch("route")
        .user_task("high", assignee="u_h")
        .user_task("mid", assignee="u_m")
        .user_task("low", assignee="u_l")
        .end(advance_policy="any")
        .connect("start", "route")
        .flow("route", "high", condition=cond("amount", "ge", 100))
        .flow("route", "mid", condition=cond("amount", "ge", 50))
        .flow("route", "low", default=True)
        .connect("high", "end")
        .connect("mid", "end")
        .connect("low", "end")
        .build()
    )
    publish(engine, definition)

    high = engine.runtime.start_process("adv_fm", start_user="u_user", variables={"amount": 200})
    assert active_of(engine, high) == {"high"}  # 两条都真，只走先声明的 high

    mid = engine.runtime.start_process("adv_fm", start_user="u_user", variables={"amount": 60})
    assert active_of(engine, mid) == {"mid"}

    low = engine.runtime.start_process("adv_fm", start_user="u_user", variables={"amount": 10})
    assert active_of(engine, low) == {"low"}


def test_validator_allows_all_join_on_exclusive_arm():
    """校验：等全部到齐 挂在「命中即走」分支臂上不再死等告警。

    v2 §2.3 可达性放行下，未选中的臂不阻塞汇聚（包容网关映射，spec §2.4），
    旧 ``advance.all_conditional_arm`` 规则已随语义修复移除。
    """
    from flowork.definitions.validator import IssueLevel, validate_definition
    from flowork.runtime.registry import create_registry

    definition = (
        FlowBuilder("adv_warn")
        .start()
        .branch("route")
        .user_task("high", assignee="u_h")
        .user_task("low", assignee="u_l")
        .user_task("merged", assignee="u_m", advance_policy="all")
        .end()
        .connect("start", "route")
        .flow("route", "high", condition=cond("amount", "gt", 100))
        .flow("route", "low", default=True)
        .connect("high", "merged")
        .connect("low", "merged")
        .connect("merged", "end")
        .build()
    )
    issues = validate_definition(definition, create_registry())
    assert "advance.all_conditional_arm" not in {i.rule for i in issues}

    # 并行臂相连（来源入边无条件）同样无错误
    definition_ok = (
        FlowBuilder("adv_ok")
        .start()
        .user_task("left", assignee="u_l")
        .user_task("right", assignee="u_r")
        .user_task("merged", assignee="u_m", advance_policy="all")
        .end()
        .connect("start", "left")
        .connect("start", "right")
        .connect("left", "merged")
        .connect("right", "merged")
        .connect("merged", "end")
        .build()
    )
    rules_ok = {
        i.rule
        for i in validate_definition(definition_ok, create_registry())
        if i.level == IssueLevel.ERROR
    }
    assert not rules_ok
