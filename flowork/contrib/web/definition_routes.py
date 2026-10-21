"""设计器定义面：节点类型元数据 / 定义 CRUD / 发布 / 校验 / 语义 diff / 版本判定预演。

``register_definition_routes(app, engine)`` 把这一族挂到应用上，无独立状态。
发布预检（``POST /api/definitions/{code}/verdict``）与 deploy 内部同一份判定——
编辑期就能看到「这次改动会被判成 major 还是 minor、有没有破坏性变更」，
发布动作本身留给流水线。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from flowork.api import (
    FloworkError,
    InvalidDefinition,
    IssueLevel,
    ProcessEngine,
    load_definition,
    semantic_diff,
)
from flowork.contrib.web._helpers import issues_payload, parse_spec

__all__ = ["FORM_EXCLUDE", "register_definition_routes"]

# 属性面板不渲染的基础字段：code 由设计器生成、kind 固定、
# hooks/properties 属于高级用法（Spec JSON Tab 直接编辑）
FORM_EXCLUDE = ("code", "kind", "hooks", "properties")


def register_definition_routes(app: FastAPI, engine: ProcessEngine) -> None:
    @app.get("/api/node-kinds")
    def node_kinds() -> list[dict[str, Any]]:
        """节点类型元数据：registry ui + Spec 的 JSON Schema（表单由此自动生成）。"""
        result: list[dict[str, Any]] = []
        for kind in engine.config.node_registry.kinds():
            registration = engine.config.node_registry.registration(kind)
            result.append(
                {
                    "kind": kind,
                    "ui": registration.ui,
                    "form_exclude": list(FORM_EXCLUDE),
                    "schema": registration.spec.model_json_schema(),
                }
            )
        return result

    @app.get("/api/definitions")
    def list_definitions() -> list[dict[str, Any]]:
        return engine.repository.list_summaries()

    @app.get("/api/definitions/{code}")
    def get_definition(code: str, source: str = "auto") -> dict[str, Any]:
        """取定义内容。``source=published`` 强制取已发布版——导出去 git 的唯一正确
        来源：drift 按发布版本内容哈希对账，导出草稿会让该 code 恒显示为未部署。
        """
        if source == "published":
            try:
                spec = engine.repository.get_definition_json(code)
                definition = engine.repository.get_definition(code)
            except FloworkError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return {
                "code": code,
                "name": definition.name,
                "draft_version": None,
                "published_version": definition.major,
                "spec": spec,
            }
        latest = engine.repository.latest_row(code)
        if latest is None:
            raise HTTPException(status_code=404, detail=f"定义 {code} 不存在")
        published_version = _latest_published_version(engine, code)
        if latest["status"] == "draft":
            draft = engine.repository.designer_draft(code)
            if draft is None:
                # latest_row 与 designer_draft 是两次独立事务，中间
                # 并发 publish 会把 draft 消费掉——回退取已发布内容（publish 竞态
                # 窗口，修前 assert 炸 500）
                draft = {"spec": engine.repository.get_definition_json(code), "layout": None}
            # 布局分离：草稿存储层 layout 独立成列,响应时合回设计器期望的形状
            spec = {**draft["spec"], "layout": draft.get("layout") or {}}
            return {
                "code": code,
                "name": latest["name"],
                "draft_version": latest["version"],
                "published_version": published_version,
                "spec": spec,
            }
        # 无草稿：返回已发布内容作为新草稿的基底
        spec = engine.repository.get_definition_json(code)
        return {
            "code": code,
            "name": latest["name"],
            "draft_version": None,
            "published_version": published_version,
            "spec": spec,
        }

    @app.put("/api/definitions/{code}")
    def save_draft(code: str, body: dict[str, Any]) -> dict[str, Any]:
        """保存草稿（最新版本已是草稿则就地覆盖，否则新建草稿版本）。"""
        body = {**body, "code": code}
        definition = parse_spec(engine, body)
        version = engine.repository.update_draft(definition)
        return {"code": code, "version": version, "status": "draft"}

    @app.post("/api/definitions/{code}/publish")
    def publish(code: str) -> dict[str, Any]:
        """发布设计器草稿（deploy：校验＋版本判定＋落库＋事件；422＝校验不过）。"""
        try:
            verdict = engine.repository.deploy_draft(code)
        except InvalidDefinition as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": "校验未通过，禁止发布", "issues": issues_payload(exc.issues)},
            ) from exc
        except FloworkError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = verdict.as_event_payload()
        return {
            "code": code,
            "version": verdict.next_major,
            "revision": verdict.next_revision,
            "breaking": verdict.breaking,
            # 版本判定全貌：破坏性明细在 triggered_rules（breaking 只是布尔）；
            # identical_to 非 None ＝ 内容与该已发布版本相同（幂等去重，未落库）
            "triggered_rules": payload["triggered_rules"],
            "identical_to": payload["identical_to"],
            "status": "published",
        }

    @app.post("/api/validate")
    def validate(body: dict[str, Any], deploy: bool = False) -> dict[str, Any]:
        """设计时校验（不落库）。body 即模板 JSON。

        ``?deploy=true`` 追加部署期校验预演（编辑期前移）：L3 引用完整性 +
        L4 目录实体存在性，把「点发布才炸」提前到编辑期以 WARNING 呈现。
        """
        definition = parse_spec(engine, body)
        issues = engine.repository.validate(definition)
        if deploy:
            issues = issues + engine.repository.validate_deploy_preview(definition)
        errors = sum(1 for issue in issues if issue.level == IssueLevel.ERROR)
        return {"ok": errors == 0, "issues": issues_payload(issues)}

    @app.post("/api/definitions/{code}/diff")
    def diff_with_published(code: str, body: dict[str, Any]) -> dict[str, Any]:
        """把请求体里的 Spec 与最新已发布版本做语义 diff（layout/版本号忽略）。

        无已发布版本时返回 first_publish=true（变更清单留空，前端按全量展示）。
        """
        definition = parse_spec(engine, body)
        try:
            base_json = engine.repository.get_definition_json(code)
        except FloworkError:
            return {"base_version": None, "first_publish": True, "has_changes": True, "changes": []}
        base = load_definition(base_json, engine.config.node_registry)
        changes = semantic_diff(base, definition)
        return {
            "base_version": base.major,
            "first_publish": False,
            "has_changes": bool(changes),
            "changes": changes,
        }

    @app.post("/api/definitions/{code}/verdict")
    def verdict_preview(code: str, body: dict[str, Any]) -> dict[str, Any]:
        """版本判定预演（只判定不落库）：下一版号 / 破坏性规则 / 幂等去重。

        CI 里「提交会发生什么」与设计器里「这份改动会被判成什么」同一份结果——
        编辑期把发布的后果前移，发布动作本身留给流水线。
        """
        definition = parse_spec(engine, body)
        try:
            preview = engine.repository.verdict(definition)
        except FloworkError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return dict(preview.as_event_payload())


def _latest_published_version(engine: ProcessEngine, code: str) -> int | None:
    try:
        return engine.repository.get_definition(code).major
    except FloworkError:
        return None
