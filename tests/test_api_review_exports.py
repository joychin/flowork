"""门面新扩符号的宿主视角回归（2026-10-21 内核 review P1-8/P1-10 落地）。

错误族与返回模型上公开面后，本文件按「宿主只 from flowork.api import」的
纪律真实消费每一个新符号：错误族钉「是 FloworkError 子类」的可编程契约，
返回模型从真实引擎调用里收割并按门面类型标注——同时它们就是死符号守卫
眼里的外部消费者。
"""

from __future__ import annotations

import pytest

from flowork.api import (
    AnticipateResult,
    DefinitionConflictError,
    EngineExecutionError,
    FloworkError,
    HistoryService,
    InputContractError,
    LockConflictError,
    NoParticipantError,
    OrgConfigError,
    Page,
    ParticipantCountMismatchError,
    ParticipantLimitError,
    ProcessEngine,
    RouteDecisionInfo,
    TaskInfo,
    TokenInfo,
    TraceEntry,
    VariableChangeInfo,
    VersionVerdict,
)
from flowork.definitions.builder import FlowBuilder


def _engine_with_flow() -> ProcessEngine:
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("api_exports")
        .start()
        .user_task("a", assignee="'u1'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    return engine


def test_escaping_error_family_is_flowork_error_subclass():
    """会从公开方法逃逸的错误族：宿主只需捕 FloworkError 基类即可编程分流。"""
    for error_cls in (
        EngineExecutionError,
        InputContractError,
        LockConflictError,
        NoParticipantError,
        ParticipantCountMismatchError,
        ParticipantLimitError,
        OrgConfigError,
        DefinitionConflictError,
    ):
        assert issubclass(error_cls, FloworkError), error_cls


def test_engine_execution_error_catchable_from_facade():
    """handler 炸 → 宿主按门面导入的 EngineExecutionError 捕获。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("api_exec_err")
        .start()
        .user_task("a", assignee="'u1'")
        .service_task("boom", "炸点", action="explode", params={})
        .end()
        .connect("start", "a")
        .connect("a", "boom")
        .connect("boom", "end")
        .build()
    )

    def explode(ctx, params):
        raise RuntimeError("外部接口炸了")

    engine.register_handler("explode", explode)
    instance_id = engine.runtime.start_process("api_exec_err", start_user="u1")
    task = engine.task.list_tasks(instance_id=instance_id)[0]
    with pytest.raises(EngineExecutionError):
        engine.task.complete(task.id, actor="u1")


def test_input_contract_error_catchable_from_facade():
    """缺必填 input → 门面导入的 InputContractError（修前宿主只能捕基类）。"""
    engine = ProcessEngine()
    definition = (
        FlowBuilder("api_contract")
        .start()
        .user_task("a", assignee="'u1'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    definition.input = {
        "type": "object",
        "properties": {"amount": {"type": "number"}},
        "required": ["amount"],
    }
    engine.repository.deploy(definition)
    with pytest.raises(InputContractError):
        engine.runtime.start_process("api_contract", start_user="u1", variables={})


def test_return_models_reachable_with_facade_types():
    """公开方法的返回模型按门面类型可用（宿主标注返回不再深路径 import）。"""
    engine = _engine_with_flow()
    instance_id = engine.runtime.start_process("api_exports", start_user="u1")

    verdict: VersionVerdict = engine.repository.verdict(
        FlowBuilder("api_exports")
        .start()
        .user_task("a", assignee="'u2'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    assert verdict.next_revision >= 1

    deployed: VersionVerdict = engine.repository.deploy(
        FlowBuilder("api_exports")
        .start()
        .user_task("a", assignee="'u2'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    assert deployed.breaking is False

    history: HistoryService = engine.history
    tokens: list[TokenInfo] = history.tokens(instance_id)
    assert tokens

    task: TaskInfo = engine.task.list_tasks(instance_id=instance_id)[0]
    anticipated: AnticipateResult = engine.task.anticipate(task.id, outcome="agree")
    assert anticipated is not None
    engine.task.complete(task.id, actor="u1")

    page: Page = history.paged_tasks(instance_id=instance_id)
    assert page.total >= 1
    trace: list[TraceEntry] = history.trace_instance(instance_id)
    assert trace
    routes: list[RouteDecisionInfo] = history.route_decisions(instance_id)
    assert routes

    changes: list[VariableChangeInfo] = engine.runtime.variable_history(instance_id)
    assert isinstance(changes, list)
