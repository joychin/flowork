"""等待节点定义与校验（wait-node.md §2/§6）：结构规则 + 位点词汇。"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable
from flowork.definitions.validator import IssueLevel, validate_definition


def error_rules(definition: ProcessDefinition, registry) -> set[str]:
    return {
        issue.rule
        for issue in validate_definition(definition, registry)
        if issue.level == IssueLevel.ERROR
    }


def _wait_flow(**wait_fields) -> FlowBuilder:
    return (
        FlowBuilder("t")
        .start()
        .wait("w", **wait_fields)
        .end()
        .connect("start", "w")
        .connect("w", "end")
    )


def test_wait_full_mode_valid(registry):
    """竞速模式（消息+超时）：零 ERROR。"""
    definition = _wait_flow(
        message={"name": "m1", "correlation_key": "instance.id"},
        output_mappings={"doc": "payload.doc_id"},
        timeout={"after_minutes": 5},
    ).build(registry)
    assert error_rules(definition, registry) == set()


def test_wait_delay_only_valid(registry):
    """纯延时模式：只声明 timeout。"""
    definition = _wait_flow(timeout={"after_minutes": 30}).build(registry)
    assert error_rules(definition, registry) == set()


def test_wait_mode_required(registry):
    """双缺：message 与 timeout 至少其一。"""
    definition = _wait_flow().build(registry)
    assert "wait.mode.required" in error_rules(definition, registry)


def test_wait_message_name_empty(registry):
    definition = _wait_flow(message={"name": "  "}).build(registry)
    assert "wait.message.name_empty" in error_rules(definition, registry)


def test_wait_exit_required(registry):
    """等待节点必须有出边。"""
    definition = (
        FlowBuilder("t")
        .start()
        .wait("w", timeout={"after_minutes": 5})
        .end()
        .connect("start", "w")
        .build(registry)
    )
    assert "wait.exit.required" in error_rules(definition, registry)


def test_wait_reject_target_forbidden(registry):
    """驳回目标不可为等待节点。"""
    definition = (
        FlowBuilder("t")
        .start()
        .user_task("a", assignee="'u1'", reject_target="w")
        .wait("w", timeout={"after_minutes": 5})
        .end()
        .connect("start", "a")
        .connect("a", "w")
        .connect("w", "end")
        .build(registry)
    )
    assert "wait.reject_target" in error_rules(definition, registry)


def test_wait_correlation_syntax_checked(registry):
    """correlation_key 语法错在发布前拦截（规则前缀 wait.correlation）。"""
    definition = _wait_flow(message={"name": "m1", "correlation_key": "1 +"}).build(registry)
    assert "wait.correlation.formula.invalid" in error_rules(definition, registry)


def test_wait_correlation_variable_checked(registry):
    """声明了变量后：correlation 引用未知变量（payload.* 不可用）→ ERROR。"""
    definition = (
        _wait_flow(message={"name": "m1", "correlation_key": "payload.doc_id"})
        .build(registry)
        .model_copy(update={"variables": [ProcessVariable(name="amount", type="number")]})
    )
    assert "wait.correlation.variable.unknown" in error_rules(definition, registry)


def test_wait_correlation_task_unavailable(registry):
    """correlation 位不提供 task.*（实例上下文求值）。"""
    definition = (
        _wait_flow(message={"name": "m1", "correlation_key": "task.assignee"})
        .build(registry)
        .model_copy(update={"variables": [ProcessVariable(name="amount", type="number")]})
    )
    assert "wait.correlation.task_unavailable" in error_rules(definition, registry)


def test_wait_vocab_only_on_wait_edges(registry):
    """wait.* 仅等待节点出边条件可用。"""
    variables = [ProcessVariable(name="amount", type="number")]
    good = (
        FlowBuilder("t")
        .start()
        .wait("w", message={"name": "m1"})
        .end()
        .connect("start", "w")
        .flow("w", "end", condition="wait.result == 'matched'")
        .build(registry)
        .model_copy(update={"variables": variables})
    )
    assert "condition.wait_source" not in error_rules(good, registry)
    bad = (
        FlowBuilder("t")
        .start()
        .user_task("a", assignee="'u1'")
        .end()
        .connect("start", "a")
        .flow("a", "end", condition="wait.result == 'matched'")
        .build(registry)
        .model_copy(update={"variables": variables})
    )
    assert "condition.wait_source" in error_rules(bad, registry)


def test_wait_output_mapping_skips_variable_check(registry):
    """output 映射右侧在消息上下文求值：payload.* 不做未声明变量检查（mapping_output 先例）。"""
    definition = (
        _wait_flow(
            message={"name": "m1"},
            output_mappings={"doc": "payload.doc_id"},
        )
        .build(registry)
        .model_copy(update={"variables": [ProcessVariable(name="amount", type="number")]})
    )
    assert "expression.variable.unknown" not in error_rules(definition, registry)
