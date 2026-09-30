"""校验器测试：好模板通过、坏模板被准确报错（边界 M0 DoD）。"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition
from flowork.definitions.validator import (
    InvalidDefinition,
    IssueLevel,
    assert_valid,
    validate_definition,
)


def error_rules(definition: ProcessDefinition, registry) -> set[str]:
    return {
        issue.rule
        for issue in validate_definition(definition, registry)
        if issue.level == IssueLevel.ERROR
    }


def test_examples_are_valid(registry, examples_dir):
    from flowork.definitions.definition import load_definition_file

    for filename in (
        "01_simple_approval.json",
        "02_countersign_branch.json",
        "03_call_activity.json",
    ):
        definition = load_definition_file(examples_dir / filename, registry)
        assert_valid(definition, registry)


def _valid_base() -> FlowBuilder:
    return FlowBuilder("t").start().end().connect("start", "end")


def test_missing_start(registry):
    definition = (
        FlowBuilder("t").user_task("a", assignee="u1").end().connect("a", "end").build(registry)
    )
    assert "structure.start.count" in error_rules(definition, registry)


def test_dangling_flow_endpoint(registry):
    definition = (
        FlowBuilder("t")
        .start()
        .end()
        .connect("start", "ghost")
        .connect("start", "end")
        .build(registry)
    )
    assert "flow.endpoint.missing" in error_rules(definition, registry)


def test_unknown_kind_rejected(registry):
    definition = (
        FlowBuilder("t")
        .start()
        .node("x", kind="no_such_kind")
        .end()
        .connect("start", "x")
        .connect("x", "end")
        .build()
    )
    assert "node.kind.unregistered" in error_rules(definition, registry)


def test_duplicate_node_code(registry):
    definition = (
        FlowBuilder("t")
        .start()
        .user_task("a", assignee="u1")
        .user_task("a", assignee="u2")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(registry)
    )
    assert "node.code.duplicate" in error_rules(definition, registry)


def test_first_match_branch_without_default(registry):
    from flowork.definitions.flow import FlowCondition

    definition = (
        FlowBuilder("t")
        .start()
        .branch("g")
        .user_task("a", assignee="u1")
        .user_task("b", assignee="u2")
        .end()
        .connect("start", "g")
        .flow(
            "g",
            "a",
            condition=FlowCondition(groups=[[{"left": "x", "operator": "eq", "right": 1}]]),
        )
        .flow("g", "b")  # 无条件且无默认 → 命中即走无法路由
        .connect("a", "end")
        .connect("b", "end")
        .build(registry)
    )
    assert "branch.first_match.no_default" in error_rules(definition, registry)


def test_advance_all_on_exclusive_arm_warns(registry):
    from flowork.definitions.flow import FlowCondition

    definition = (
        FlowBuilder("t")
        .start()
        .branch("g")
        .user_task("a", assignee="u1")
        .user_task("m", assignee="u2", advance_policy="all")
        .end()
        .connect("start", "g")
        .flow(
            "g",
            "a",
            condition=FlowCondition(groups=[[{"left": "x", "operator": "eq", "right": 1}]]),
        )
        .flow("g", "m", default=True)
        .connect("a", "m")
        .connect("m", "end")
        .build(registry)
    )
    rules = error_rules(definition, registry)
    # 旧「并行网关无条件出边」规则随节点类型移除；改为开始条件死等告警
    all_rules = {issue.rule for issue in validate_definition(definition, registry)}
    assert "advance.all_conditional_arm" in all_rules
    assert "structure.node.unreachable" not in rules


def test_deadend_node(registry):
    definition = (
        FlowBuilder("t")
        .start()
        .user_task("a", assignee="u1")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .user_task("orphan", assignee="u2")  # 无出边
        .build(registry)
    )
    assert "structure.node.deadend" in error_rules(definition, registry)


def test_condition_groups_and_formula_both_set(registry):
    from flowork.definitions.flow import FlowCondition

    definition = (
        FlowBuilder("t")
        .start()
        .branch("g")
        .end()
        .connect("start", "g")
        .flow(
            "g",
            "end",
            condition=FlowCondition(
                groups=[[{"left": "x", "operator": "eq", "right": 1}]], formula="x == 1"
            ),
        )
        .flow("g", "end", default=True)
        .build(registry)
    )
    assert "condition.both_set" in error_rules(definition, registry)


def test_reject_target_missing(registry):
    definition = (
        FlowBuilder("t")
        .start()
        .user_task("a", assignee="u1", reject_target="ghost")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(registry)
    )
    assert "user_task.reject_target.missing" in error_rules(definition, registry)


def test_assert_valid_raises_with_all_errors(registry):
    definition = (
        FlowBuilder("t").user_task("a", assignee="u1").end().connect("a", "end").build(registry)
    )
    with pytest.raises(InvalidDefinition) as excinfo:
        assert_valid(definition, registry)
    assert any(i.rule == "structure.start.count" for i in excinfo.value.issues)
