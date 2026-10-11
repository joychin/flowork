"""审计数据面（设计器插件计划批 A）：整图模拟 / 实例只读 / 版本面。

从 app.py 拆出（300 行红线，preview_routes 先例）；``register_audit_routes(app,
engine)`` 挂端点，无独立状态。资源命名与参数风格对齐 kernel-v2 spec §12 已定稿
的 /v2 面（``GET /definitions/{code}?major=&revision=`` 同义）；全部只读（除模拟
外零副作用），模拟走内核 simulated 实例（隔离语义见 simulation-dryrun-design）。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import HTTPException

from flowork.api import FloworkError, IssueLevel
from flowork.contrib.web._helpers import issues_payload, parse_spec

__all__ = ["register_audit_routes"]


def register_audit_routes(app: Any, engine: Any) -> None:
    """把模拟 / 实例只读 / 版本端点挂到应用（engine 为 ProcessEngine）。"""

    @app.post("/api/definitions/{code}/simulate")
    def simulate(
        code: str, body: dict[str, Any], major: int | None = None, revision: int | None = None
    ) -> dict[str, Any]:
        """整图模拟（批 A1，草稿主场景）：证据先于发布。

        body：``{"spec": 定义 JSON（可选——缺省取已保存草稿）, "variables": 样例}``。
        携带 spec 或取草稿时走**草稿直通**（不落定义表，模拟前强制设计期校验，
        ERROR 拦 422）；``?major=&revision=`` 则模拟已发布版本（对照线上行为）。
        """
        raw_spec = body.get("spec")
        raw_vars = body.get("variables")
        variables = raw_vars if isinstance(raw_vars, dict) else None
        if raw_spec is not None or (major is None and revision is None):
            spec = raw_spec if isinstance(raw_spec, dict) else _saved_draft(engine, code)
            definition = parse_spec(engine, spec)
            issues = engine.repository.validate(definition)
            errors = [i for i in issues if i.level == IssueLevel.ERROR]
            if errors:
                raise HTTPException(
                    status_code=422,
                    detail={"message": "校验未通过，禁止模拟", "issues": issues_payload(errors)},
                )
            report = engine.runtime.simulate(code, spec=spec, variables=variables)
        else:
            report = engine.runtime.simulate(
                code, variables=variables, major=major, revision=revision
            )
        return asdict(report)

    @app.get("/api/instances")
    def instances(
        state: str | None = None,
        definition_code: str | None = None,
        limit: int = 100,
        include_simulated: bool = False,
    ) -> list[dict[str, Any]]:
        """实例列表（审计入口）。默认隔离模拟实例——试跑不进业务视图是语义不是 bug。"""
        return [
            inst.model_dump(mode="json")
            for inst in engine.history.instances(
                state=state,
                definition_code=definition_code,
                limit=limit,
                include_simulated=include_simulated,
            )
        ]

    @app.get("/api/instances/{instance_id}")
    def instance_detail(instance_id: str) -> dict[str, Any]:
        """实例详情：实例 + 令牌状态 + 当前变量值（审计三面板的头部数据）。"""
        try:
            instance = engine.runtime.get_instance(instance_id)
            tokens = engine.history.tokens(instance_id)
            variables = engine.runtime.variables_of(instance_id)
        except FloworkError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "instance": instance.model_dump(mode="json"),
            "tokens": [token.model_dump(mode="json") for token in tokens],
            "variables": variables,
        }

    @app.get("/api/instances/{instance_id}/timeline")
    def timeline(instance_id: str) -> list[dict[str, Any]]:
        """实例流转轨迹：令牌 + 已办 + 事件按时间合并（审批轨迹页数据源）。"""
        try:
            entries = engine.history.trace_instance(instance_id)
        except FloworkError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [entry.model_dump(mode="json") for entry in entries]

    @app.get("/api/instances/{instance_id}/route-decisions")
    def route_decisions(instance_id: str) -> list[dict[str, Any]]:
        """路由决策审计（F3）：每条条件边为什么这么选 + 当时求值快照。"""
        try:
            rows = engine.history.route_decisions(instance_id)
        except FloworkError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [row.model_dump(mode="json") for row in rows]

    @app.get("/api/instances/{instance_id}/variable-history")
    def variable_history(
        instance_id: str, name: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """变量血缘（spec §11）：改前/改后 + 写方归因，倒序可按键过滤。"""
        try:
            changes = engine.runtime.variable_history(
                instance_id, name=name, limit=limit, offset=offset
            )
        except FloworkError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [asdict(change) for change in changes]

    @app.get("/api/instances/{instance_id}/tasks")
    def instance_tasks(instance_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """实例已办任务（分页）。"""
        result: dict[str, Any] = engine.history.paged_tasks(
            instance_id=instance_id, offset=offset, limit=limit
        ).model_dump(mode="json")
        return result

    @app.get("/api/definitions/{code}/versions")
    def versions(code: str) -> list[dict[str, Any]]:
        """已发布版本列表（新→旧）：版本号 / 破坏性标记 / 发布时间与归因。"""
        rows: list[dict[str, Any]] = engine.repository.published_versions(code)
        if not rows:
            raise HTTPException(status_code=404, detail=f"定义 {code} 无已发布版本")
        return rows

    @app.get("/api/definitions/{code}/versions/{major}")
    def version_detail(code: str, major: int, revision: int | None = None) -> dict[str, Any]:
        """按版本取已发布 spec（spec §12 ``GET /definitions/{code}?major=`` 同义；
        只读模式（C2）没有草稿，本端点是 readonly 视图的数据前提）。"""
        try:
            spec: dict[str, Any] = engine.repository.get_definition_json(code, major, revision)
        except FloworkError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return spec


def _saved_draft(engine: Any, code: str) -> dict[str, Any]:
    """取已保存草稿；无草稿且无已发布版本时报 404（有发布版则提示给 major）。"""
    draft: dict[str, Any] | None = engine.repository.designer_draft(code)
    if draft is not None:
        spec: dict[str, Any] = draft["spec"]
        return spec
    try:
        engine.repository.get_definition_json(code)
    except FloworkError as exc:
        raise HTTPException(status_code=404, detail=f"定义 {code} 不存在") from exc
    raise HTTPException(
        status_code=404,
        detail=f"定义 {code} 无待发布草稿（模拟已发布版本请携带 ?major= 参数）",
    )
