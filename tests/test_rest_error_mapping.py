"""REST 错误族 → HTTP 状态码契约（来源：全仓review-2026-11-23 §2.3）。

DefinitionConflictError 的 docstring 承诺「REST 映射 409」但映射表漏登记、
isinstance 链又不命中——落 500：客户端把可重试的并发部署冲突当服务器故障
告警。本文件把「flowork.api 导出的每个错误类的状态码」钉成显式契约：
新增导出错误类不登记映射即红灯。
"""

from __future__ import annotations

import inspect

from fastapi import FastAPI
from fastapi.testclient import TestClient

import flowork.api
from flowork.contrib.rest import register_rest_routes
from flowork.contrib.rest._common import status_of
from flowork.errors import (
    DefinitionConflictError,
    DefinitionError,
    FloworkError,
    InstanceNotFoundError,
    NotFoundError,
    TaskNotAvailableError,
)

# 导出错误类 → 期望状态码（isinstance 链 + 类名兜底表共同作用的最终落点）。
# 500 判据：引擎/宿主侧技术故障（调用方无从修正，重试同类请求同样失败）；
# 4xx 判据：调用方可修正（重试/改参数/改定义）。
EXPECTED: dict[str, int] = {
    # —— 4xx：可重试或可修正 ——
    "ConditionError": 422,
    "DefinitionConflictError": 409,
    "DefinitionError": 422,
    "DefinitionNotPublishedError": 404,
    "DefinitionRetiredError": 404,
    "DuplicateStartError": 409,
    "InputContractError": 422,
    "InstanceNotFoundError": 404,
    "InvalidDefinition": 422,
    "InvalidDefinitionData": 422,
    "JobStateError": 409,
    "LockConflictError": 409,  # 重试锁冲突是唯一正确的后续动作
    "NotFoundError": 404,
    "ParticipantCountMismatchError": 422,
    "ParticipantLimitError": 422,
    "TaskNotAvailableError": 409,
    "UnknownHandler": 500,  # 运行期已归一 EngineExecutionError（挂起+故障单）；裸类逃逸＝技术故障
    "UnknownNodeKind": 422,
    # —— 500：服务侧技术故障 ——
    "DirectoryError": 500,  # 目录装配/读取故障（YAML 坏、库连接问题）
    "EngineExecutionError": 500,  # 节点执行期技术故障（hook/handler 异常）
    "FloworkError": 500,  # 基类兜底
    "FormulaError": 422,  # CEL 公式语法/护栏拒绝——改定义即可修
    "NoParticipantError": 500,  # 解析期技术故障（已挂起+故障单）
    "OrgConfigError": 500,  # 目录装配错误
}


def _exported_error_classes() -> list[str]:
    names = []
    for name in flowork.api.__all__:
        obj = getattr(flowork.api, name, None)
        if inspect.isclass(obj) and issubclass(obj, FloworkError):
            names.append(name)
    return names


def test_every_exported_error_has_a_declared_status():
    """导出错误类与期望映射集合相等：多一个不登记即红，删类同步清表。"""
    exported = set(_exported_error_classes())
    assert exported == set(EXPECTED), (
        f"映射表与 flowork.api 导出错误类不一致；"
        f"未登记：{sorted(exported - set(EXPECTED))}，"
        f"已不存在：{sorted(set(EXPECTED) - exported)}"
    )


def test_status_matches_for_every_exported_error():
    for name, expected in EXPECTED.items():
        cls = getattr(flowork.api, name)
        # 抛实例化（部分错误类构造需参数）
        try:
            exc = cls("probe")
        except TypeError:
            exc = cls.__new__(cls)
            FloworkError.__init__(exc, "probe")
        assert status_of(exc) == expected, f"{name} → {status_of(exc)}，期望 {expected}"


def test_isinstance_chain_subclasses_keep_their_family_status():
    """子类关系决定族状态：NotFoundError 族 404、任务族 409、定义族 422。"""
    assert status_of(InstanceNotFoundError("x")) == 404
    assert status_of(NotFoundError("x")) == 404
    assert status_of(TaskNotAvailableError("x")) == 409
    assert status_of(DefinitionError("x")) == 422


def test_deploy_conflict_surfaces_as_409_over_rest(tmp_path):
    """端到端：deploy 抛并发冲突 → REST 409 + 统一错误体（修前 500）。"""
    from flowork.api import EngineConfig, ProcessEngine

    engine = ProcessEngine(EngineConfig(db_file=tmp_path / "conflict.db"))

    def conflicting(*_a, **_k):
        raise DefinitionConflictError("并发部署撞 (code, major, revision) 唯一索引——重试即幂等")

    engine.repository.deploy = conflicting  # type: ignore[method-assign]
    app = FastAPI()
    app.state.engine = engine
    register_rest_routes(app, engine)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/v2/definitions/deploy",
        json={"spec": {"code": "x", "nodes": [], "flows": []}, "actor": "u"},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "DefinitionConflictError"
