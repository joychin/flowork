"""公开面契约：flowork.api 的符号快照与可解析性。

快照红=公开面发生了增删——这是**显式**动作：扩面/收缩须同步本文件快照，
并在 AGENTS.md 当前快照里记一笔；对宿主与 AI 而言 flowork.api 的符号即承诺。
"""

from __future__ import annotations

import flowork.api as api

# 冻结快照（字母序，与 api.__all__ 逐字对齐）
EXPECTED_SURFACE = frozenset(
    {
        "ConditionError",
        "DefinitionError",
        "DefinitionNotPublishedError",
        "DefinitionRetiredError",
        "DuplicateStartError",
        "EngineConfig",
        "EventInfo",
        "ExpressionFunction",
        "ExpressionFunctions",
        "FlowBuilder",
        "FlowNode",
        "FloworkError",
        "FormulaError",
        "FunctionEntry",
        "FunctionSignature",
        "HistoryService",
        "InMemoryNodeContext",
        "InstanceInfo",
        "InstanceNotFoundError",
        "InstanceState",
        "InvalidDefinition",
        "InvalidDefinitionData",
        "IssueLevel",
        "JobClaim",
        "JobService",
        "JobStateError",
        "MessageService",
        "MultiInstance",
        "NodeBehavior",
        "NodeContext",
        "NodeRegistry",
        "NotFoundError",
        "OrgDirectoryLike",
        "ProcessDefinition",
        "ProcessVariable",
        "ProcessEngine",
        "RepositoryService",
        "RouteMode",
        "RuntimeService",
        "StaticOrgDirectory",
        "TaskCompleteResult",
        "TaskHistoryInfo",
        "TaskInfo",
        "TaskNotAvailableError",
        "TaskService",
        "TokenState",
        "UnknownHandler",
        "UnknownNodeKind",
        "ValidateIssue",
        "create_registry",
        "engine_functions",
        "evaluate_condition",
        "evaluate_formula_value",
        "expression_functions_of",
        "identifier_entries",
        "load_definition",
        "load_definition_file",
        "reserved_identifiers",
        "semantic_diff",
        "simulate_route",
        "utc_now",
        "validate_definition",
    }
)


def test_api_surface_snapshot() -> None:
    assert set(api.__all__) == EXPECTED_SURFACE, (
        "flowork.api 公开面增删须显式：同步本快照 + AGENTS.md 记录。\n"
        f"新增: {sorted(set(api.__all__) - EXPECTED_SURFACE)}\n"
        f"移除: {sorted(EXPECTED_SURFACE - set(api.__all__))}"
    )


def test_api_exports_resolve_to_real_objects() -> None:
    for name in api.__all__:
        assert getattr(api, name, None) is not None, f"公开面符号失效：{name}"
