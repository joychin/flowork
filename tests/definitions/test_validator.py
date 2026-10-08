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
        FlowBuilder("t").user_task("a", assignee="'u1'").end().connect("a", "end").build(registry)
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
        .user_task("a", assignee="'u1'")
        .user_task("a", assignee="'u2'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(registry)
    )
    assert "node.code.duplicate" in error_rules(definition, registry)


def test_first_match_branch_without_default(registry):

    definition = (
        FlowBuilder("t")
        .start()
        .branch("g")
        .user_task("a", assignee="'u1'")
        .user_task("b", assignee="'u2'")
        .end()
        .connect("start", "g")
        .flow("g", "a", condition="x == 1")
        .flow("g", "b")  # 无条件且无默认 → 命中即走无法路由
        .connect("a", "end")
        .connect("b", "end")
        .build(registry)
    )
    assert "branch.first_match.no_default" in error_rules(definition, registry)


def test_advance_all_on_exclusive_arm_no_warning(registry):
    """v2 §2.3 可达性放行：all 汇聚挂「命中即走」分支臂不再死等告警（规则已移除）。"""

    definition = (
        FlowBuilder("t")
        .start()
        .branch("g")
        .user_task("a", assignee="'u1'")
        .user_task("m", assignee="'u2'", advance_policy="all")
        .end()
        .connect("start", "g")
        .flow("g", "a", condition="x == 1")
        .flow("g", "m", default=True)
        .connect("a", "m")
        .connect("m", "end")
        .build(registry)
    )
    rules = error_rules(definition, registry)
    all_rules = {issue.rule for issue in validate_definition(definition, registry)}
    assert "advance.all_conditional_arm" not in all_rules
    assert "structure.node.unreachable" not in rules


def test_deadend_node(registry):
    definition = (
        FlowBuilder("t")
        .start()
        .user_task("a", assignee="'u1'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .user_task("orphan", assignee="u2")  # 无出边
        .build(registry)
    )
    assert "structure.node.deadend" in error_rules(definition, registry)


def test_condition_is_plain_formula(registry):
    """单轨化后条件就是 CEL 字符串：合法公式不再有 both_set/互斥类问题。"""

    definition = (
        FlowBuilder("t")
        .start()
        .branch("g")
        .end()
        .connect("start", "g")
        .flow("g", "end", condition="x == 1")
        .flow("g", "end", default=True)
        .build(registry)
    )
    rules = error_rules(definition, registry)
    assert "condition.formula.invalid" not in rules  # 单轨：合法公式零问题


def test_reject_target_missing(registry):
    definition = (
        FlowBuilder("t")
        .start()
        .user_task("a", assignee="'u1'", reject_target="ghost")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(registry)
    )
    assert "user_task.reject_target.missing" in error_rules(definition, registry)


def test_assert_valid_raises_with_all_errors(registry):
    definition = (
        FlowBuilder("t").user_task("a", assignee="'u1'").end().connect("a", "end").build(registry)
    )
    with pytest.raises(InvalidDefinition) as excinfo:
        assert_valid(definition, registry)
    assert any(i.rule == "structure.start.count" for i in excinfo.value.issues)


# —— CEL 静态检查薄层（kernel-v2 §4.3）——


def _cel_flow(registry, *, formula=None, declare=("amount", "urgent")):
    from flowork.definitions.definition import ProcessVariable

    definition = (
        FlowBuilder("cel")
        .start()
        .branch("gate")
        .user_task("big", assignee="'u_boss'")
        .user_task("small", assignee="'u_clerk'")
        .end()
        .connect("start", "gate")
        .flow("gate", "big", condition=formula)
        .flow("gate", "small", default=True)
        .connect("big", "end")
        .connect("small", "end")
        .build(registry)
    )
    definition.variables = [ProcessVariable(name=name, type="number") for name in declare]
    return definition


class _FixedFunctions:
    """测试用 ExpressionFunctions 最小实现（固定注册表）。"""

    def __init__(self, table):
        self._table = table

    def functions(self):
        return self._table


def _registry_with(manager_params: int = 1):
    from flowork.spi.ports import ExpressionFunction, FunctionSignature

    return _FixedFunctions(
        {
            "manager_of": ExpressionFunction(
                FunctionSignature(("one_user",) * manager_params, "one_user"), lambda u: u
            )
        }
    )


def test_cel_formula_parse_error_is_error(registry):
    definition = _cel_flow(registry, formula="amount >")
    assert "condition.formula.invalid" in error_rules(definition, registry)


def test_cel_formula_unknown_variable_is_error(registry):
    definition = _cel_flow(registry, formula="nope > 1")
    assert "condition.variable.unknown" in error_rules(definition, registry)


def test_cel_formula_declared_references_pass(registry):
    definition = _cel_flow(registry, formula="amount > 100 && urgent >= 1")
    rules = error_rules(definition, registry)
    assert "condition.variable.unknown" not in rules
    assert "condition.formula.invalid" not in rules


def test_cel_builtin_function_and_initiator_pass(registry):
    definition = _cel_flow(
        registry, formula='items.size() > 1 && initiator == "u1"', declare=("items",)
    )
    rules = error_rules(definition, registry)
    assert "condition.variable.unknown" not in rules
    assert "condition.function.unknown" not in rules


def test_cel_unknown_function_with_registry_is_error(registry):
    functions = _registry_with()
    definition = _cel_flow(registry, formula="nosuchfn(amount) == 1")
    issues = validate_definition(definition, registry, expression_functions=functions)
    matching = [i for i in issues if i.rule == "condition.function.unknown"]
    assert matching and matching[0].level == IssueLevel.ERROR


def test_cel_unknown_function_without_registry_warns(registry):
    definition = _cel_flow(registry, formula="nosuchfn(amount) == 1")
    issues = validate_definition(definition, registry)
    matching = [i for i in issues if i.rule == "condition.function.unknown"]
    assert matching and matching[0].level == IssueLevel.WARNING


def test_cel_function_arity_mismatch_is_error(registry):
    functions = _registry_with(manager_params=1)
    definition = _cel_flow(registry, formula='manager_of(initiator, amount) == "u"')
    issues = validate_definition(definition, registry, expression_functions=functions)
    assert any(i.rule == "condition.function.arity" and i.level == IssueLevel.ERROR for i in issues)


def test_cel_registered_function_passes(registry):
    functions = _registry_with(manager_params=1)
    definition = _cel_flow(registry, formula='manager_of(initiator) == "u:li"')
    issues = validate_definition(definition, registry, expression_functions=functions)
    assert not any(
        i.rule.startswith("condition.function") and i.level == IssueLevel.ERROR for i in issues
    )


def test_reserved_identifier_initiator_cannot_be_declared(registry):
    definition = _cel_flow(registry, formula="amount > 1", declare=("amount", "initiator"))
    assert "variables.reserved_name" in error_rules(definition, registry)


def test_reserved_identifier_now_cannot_be_declared(registry):
    """expression-dataface E3：now 由引擎注入，不可声明为流程变量。"""
    definition = _cel_flow(registry, formula="amount > 1", declare=("amount", "now"))
    assert "variables.reserved_name" in error_rules(definition, registry)


def test_now_reference_needs_no_declaration(registry):
    """now 为全位点保留词：未声明也不算 unknown（目录表驱动放行）。"""
    definition = _cel_flow(registry, formula="now > timestamp('2000-01-01T00:00:00Z')")
    issues = validate_definition(definition, registry)
    assert not any(issue.rule == "condition.variable.unknown" for issue in issues)


# —— 位点统一静态检查（spec §4.2：取人/改派/映射位与连线同一套检查）——


def _subjects_flow(registry, *, assignee="'u_boss'", declare=("amount",)):
    from flowork.definitions.definition import ProcessVariable

    definition = (
        FlowBuilder("subj")
        .start()
        .user_task("a", assignee=assignee)
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build(registry)
    )
    definition.variables = [ProcessVariable(name=name, type="number") for name in declare]
    return definition


def test_participant_unknown_variable_is_error(registry):
    definition = _subjects_flow(registry, assignee="nosuchvar")
    assert "expression.variable.unknown" in error_rules(definition, registry)


def test_participant_predicate_root_returns_rejected(registry):
    """谓词函数（returns=bool）误用取人位：根调用类型对位提前拦（有注册表时）。"""
    from flowork.definitions.validator import IssueLevel, validate_definition
    from flowork.runtime.registry import create_registry
    from flowork.spi.ports import ExpressionFunction, FunctionSignature

    class _Registry:
        def functions(self):
            return {
                "has_role": ExpressionFunction(
                    FunctionSignature(("one_user", "string"), "bool"), lambda u, r: True
                )
            }

    definition = _subjects_flow(create_registry(), assignee='has_role(initiator, "x")')
    issues = validate_definition(
        definition,
        create_registry(),
        expression_functions=_Registry(),  # type: ignore[arg-type]
    )
    hits = [i for i in issues if i.rule == "expression.function.returns"]
    assert hits and hits[0].level == IssueLevel.ERROR


def test_task_snapshot_only_available_in_reassign(registry):
    from flowork.definitions.definition import ProcessVariable
    from flowork.definitions.node import TaskDeadline

    definition = (
        FlowBuilder("taskpos")
        .start()
        .user_task(
            "a",
            assignee="'u_boss'",
            deadlines=[
                TaskDeadline(
                    after_minutes=5, action="reassign", reassign_to="manager_of(task.assignee)"
                )
            ],
        )
        .user_task("b", assignee="task.assignee")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build(registry)
    )
    definition.variables = [ProcessVariable(name="amount", type="number")]
    rules = error_rules(definition, registry)
    assert "expression.task_unavailable" in rules  # 取人位不可用 task.*
    assert "expression.variable.unknown" not in rules  # 改派位的 task.* 合法


def test_mapping_output_skips_parent_variable_check(registry):
    """输出映射在子上下文求值：父定义不做未声明变量检查（子变量静态不可知）。"""
    definition = (
        FlowBuilder("mapout")
        .start()
        .call_activity("sub", process_code="child", output_mappings={"r": "child_only_var"})
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build(registry)
    )
    assert "expression.variable.unknown" not in error_rules(definition, registry)


def test_mapping_input_unknown_variable_is_error(registry):
    from flowork.definitions.definition import ProcessVariable

    definition = (
        FlowBuilder("mapin")
        .start()
        .call_activity("sub", process_code="child", input_mappings={"x": "nosuchvar"})
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build(registry)
    )
    definition.variables = [ProcessVariable(name="amount", type="number")]
    assert "expression.variable.unknown" in error_rules(definition, registry)
