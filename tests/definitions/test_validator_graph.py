"""图级 lint（能力补足 P0-3）：无出口子图 / all 汇聚死臂——全 WARNING 不拦发布。

判据与运行时 join gate 同源（definitions/graph.py 共用遍历）；防误报回归：
条件未命中臂**不**告警（不复活已移除的 advance.all_conditional_arm 判据）。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable
from flowork.definitions.validator import IssueLevel, validate_definition


def _warning_rules(definition: ProcessDefinition, registry) -> dict[str, list[str]]:
    issues = validate_definition(definition, registry)
    warned: dict[str, list[str]] = {}
    for issue in issues:
        if issue.level == IssueLevel.WARNING:
            warned.setdefault(issue.rule, []).append(issue.node or "")
    return warned


def _no_exit_flow() -> ProcessDefinition:
    """start→a→end 主路 + a→b→c→b 封闭环（b/c 可达但到不了 end）。"""
    return (
        FlowBuilder("no_exit")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .user_task("c", assignee="'u_c'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .connect("a", "b")
        .connect("b", "c")
        .connect("c", "b")
        .build()
    )


def test_closed_subgraph_without_exit_warns(registry):
    """封闭子图（成环整体无出口）逐节点 WARNING；单点死路不重复报。"""
    warned = _warning_rules(_no_exit_flow(), registry)
    assert warned.get("graph.subgraph.no_exit") == ["b", "c"]
    assert "a" not in warned.get("graph.subgraph.no_exit", [])
    # 全 WARNING：不产生 ERROR（发布拦截面不变）
    issues = validate_definition(_no_exit_flow(), registry)
    assert not [
        i for i in issues if i.rule == "graph.subgraph.no_exit" and i.level == IssueLevel.ERROR
    ]


def test_deploy_allows_graph_lint_warnings(tmp_path):
    """带无出口子图的定义仍可发布（WARNING 不拦）——lint 是质量告警非正确性拦截。"""
    from flowork.engine.config import EngineConfig
    from flowork.engine.engine import ProcessEngine

    engine = ProcessEngine(EngineConfig(db_file=tmp_path / "lint.db"))
    try:
        engine.repository.deploy(_no_exit_flow())
        assert engine.repository.get_definition("no_exit") is not None
    finally:
        engine.close()


def _dead_arm_flow() -> ProcessDefinition:
    """并行双臂 all 汇聚 + 孤儿节点 orphan→join（臂源不可达）。"""
    return (
        FlowBuilder("dead_arm")
        .start()
        .user_task("p1", assignee="'u1'")
        .user_task("p2", assignee="'u2'")
        .user_task("join", assignee="'u_j'", advance_policy="all")
        .user_task("orphan", assignee="'u_x'")
        .end()
        .connect("start", "p1")
        .connect("start", "p2")
        .connect("p1", "join")
        .connect("p2", "join")
        .connect("join", "end")
        .flow("orphan", "join")
        .build()
    )


def test_all_join_dead_arm_warns(registry):
    """all 汇聚的入边源不可达 → 该臂永远等不来令牌（定位到节点+连线）。"""
    issues = validate_definition(_dead_arm_flow(), registry)
    matching = [i for i in issues if i.rule == "join.arm.unreachable"]
    assert len(matching) == 1
    assert matching[0].node == "join" and matching[0].flow == "orphan->join"
    assert matching[0].level == IssueLevel.WARNING
    # 孤儿节点本身仍由既有结构规则提示（互补不替代）
    assert any(i.rule == "structure.node.unreachable" and i.node == "orphan" for i in issues)


def test_healthy_parallel_all_join_has_no_graph_warnings(registry):
    """正常并行双臂 all 汇聚零图级告警（防误报）。"""
    definition = (
        FlowBuilder("healthy_join")
        .start()
        .user_task("p1", assignee="'u1'")
        .user_task("p2", assignee="'u2'")
        .user_task("join", assignee="'u_j'", advance_policy="all")
        .end()
        .connect("start", "p1")
        .connect("start", "p2")
        .connect("p1", "join")
        .connect("p2", "join")
        .connect("join", "end")
        .build()
    )
    issues = validate_definition(definition, registry)
    assert not [i for i in issues if i.rule.startswith(("graph.", "join."))]


def test_conditional_arm_into_all_join_not_warned(registry):
    """条件未命中臂**不**告警——运行时会落定放行（不复活旧判据回归）。"""
    definition = (
        FlowBuilder("cond_arm")
        .start()
        .user_task("p1", assignee="'u1'")
        .user_task("p2", assignee="'u2'")
        .user_task("join", assignee="'u_j'", advance_policy="all")
        .end()
        .connect("start", "p1")
        .connect("start", "p2")
        .flow("p1", "join", condition="amount > 100")
        .connect("p2", "join")
        .connect("join", "end")
        .build()
    )
    definition.variables = [ProcessVariable(name="amount", type="number")]
    issues = validate_definition(definition, registry)
    assert not [i for i in issues if i.rule.startswith(("graph.", "join."))]


def test_missing_start_or_end_skips_graph_lints(registry):
    """无 start/end 时图级 lint 跳过（结构规则已拦，不二次报）。"""
    definition = (
        FlowBuilder("no_start").user_task("a", assignee="'u_a'").end().connect("a", "end").build()
    )
    issues = validate_definition(definition, registry)
    assert not [i for i in issues if i.rule.startswith(("graph.", "join."))]
    assert any(i.rule == "structure.start.count" for i in issues)


@pytest.mark.parametrize(
    "builder_code",
    ["no_exit", "dead_arm"],
)
def test_graph_rules_are_warning_only(registry, builder_code):
    """两条图级规则全量 WARNING：任何构造下不产生 ERROR。"""
    definition = {"no_exit": _no_exit_flow, "dead_arm": _dead_arm_flow}[builder_code]()
    issues = validate_definition(definition, registry)
    assert all(
        i.level == IssueLevel.WARNING for i in issues if i.rule.startswith(("graph.", "join."))
    )
