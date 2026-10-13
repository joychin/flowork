"""公开面契约：flowork.api 的符号快照与可解析性。

快照红=公开面发生了增删——这是**显式**动作：扩面/收缩须同步本文件快照，
并在 AGENTS.md 当前快照里记一笔；对宿主与 AI 而言 flowork.api 的符号即承诺。
"""

from __future__ import annotations

import flowork.api as api

# 冻结快照（字母序，与 api.__all__ 逐字对齐）
EXPECTED_SURFACE = frozenset(
    {
        "API_VERSION",
        "ConditionError",
        "DefinitionError",
        "DefinitionNotPublishedError",
        "DefinitionRetiredError",
        "DirectoryError",
        "DirectoryStore",
        "DomainEvent",
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
        "IdempotentSink",
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
        "OutboxConsumer",
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


def test_directory_store_reachable_via_facade() -> None:
    """收尾批 5 冒烟：DB 版目录经门面可达——构造、挂 EngineConfig.org、跑通候选认领。"""
    from flowork.api import DirectoryStore, FlowBuilder, ProcessEngine

    spec = (
        FlowBuilder("apismoke")
        .start()
        .user_task("a", candidate_users=['"u_cand"'])
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine = ProcessEngine()
    store = DirectoryStore(engine.session_factory)  # 与引擎共用同一会话工厂
    store.create_user("u_cand", name="候选人")
    engine.repository.deploy(spec)
    engine.config.org = store  # 装配面：DB 目录即 OrgDirectoryLike
    iid = engine.runtime.start_process("apismoke", start_user="u")
    task = engine.task.list_tasks(instance_id=iid)[0]
    assert task.assignee == ""
    engine.task.claim(task.id, "u_cand")  # 组/候选判定走 store
    assert engine.task.list_tasks(assignee="u_cand")[0].id == task.id
