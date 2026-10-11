"""FastAPI 应用工厂：定义 CRUD / 校验 / 发布 / 节点类型元数据。

启动::

    uvicorn flowork.contrib.web.app:app --port 8901     # FLOWORK_DB 指定 SQLite 文件
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from flowork.api import (
    DefinitionError,
    EngineConfig,
    FloworkError,
    InvalidDefinition,
    IssueLevel,
    ProcessEngine,
    load_definition,
    load_definition_file,
    semantic_diff,
)
from flowork.contrib.web._helpers import issues_payload, parse_spec
from flowork.contrib.web.audit_routes import register_audit_routes
from flowork.contrib.web.meta_routes import register_meta_routes
from flowork.contrib.web.preview_routes import register_preview_routes

# "app" 为惰性属性(模块 __getattr__ 按需创建,import 无副作用),不在 __all__
__all__ = ["create_app"]

# 属性面板不渲染的基础字段：code 由设计器生成、kind 固定、
# hooks/properties 属于高级用法（Spec JSON Tab 直接编辑）
FORM_EXCLUDE = ("code", "kind", "hooks", "properties")

EXAMPLES_DIR = Path(__file__).parent.parent.parent.parent / "examples"


def create_app(db_file: str | Path | None = None, *, seed: bool = True) -> FastAPI:
    """创建应用。``db_file`` 缺省取 FLOWORK_DB 环境变量，再缺省 designer.db。

    ``FLOWORK_DB`` 可给 SQLite 文件路径，也可给完整数据库 URL（如
    ``postgresql+psycopg://user:pw@host/db``，spec §14 支持矩阵）。超时 Job
    调度默认随应用启动（多副本部署也安全：tick 内 CAS 认领，同一 Job 只会被
    一个副本执行）。环境变量：``FLOWORK_JOBS=off`` 关闭内置调度（改用外部
    定时器调 ``flowork tick``）；``FLOWORK_JOB_INTERVAL`` 轮询间隔秒数（默认 30）。
    """
    import os

    db_path = db_file or os.environ.get("FLOWORK_DB", "designer.db")
    config_kwargs: dict[str, Any] = {"db_file": db_path}
    org_path = os.environ.get("FLOWORK_ORG")
    if org_path:  # 批 D1：目录只读下发面(FLOWORK_ORG=组织 yaml,同 org_directory 约定)
        from flowork.api import StaticOrgDirectory

        config_kwargs["org"] = StaticOrgDirectory.from_yaml(org_path)
    engine = ProcessEngine(EngineConfig(**config_kwargs))
    app = FastAPI(title="flowork designer API", version="0.1.0")
    app.state.engine = engine
    jobs_enabled = os.environ.get("FLOWORK_JOBS", "on").lower() not in ("off", "0", "false")
    job_interval = int(os.environ.get("FLOWORK_JOB_INTERVAL", "30"))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 插件库托管（批 C1 单进程 demo 模式）：designer/dist-lib 存在时挂载——
    # 先 `npm run build:lib`，再起服务即得 /designer-demo 只读嵌入示例页
    lib_dir = Path(__file__).resolve().parent.parent.parent.parent / "designer" / "dist-lib"
    if lib_dir.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/designer-lib", StaticFiles(directory=lib_dir), name="designer-lib")

        @app.get("/designer-demo", response_class=HTMLResponse)
        def designer_demo(code: str | None = None) -> str:
            """三行代码嵌入的只读审计示例（?code=xxx 深链到指定定义）。"""
            deep = f", definitionCode: '{code}'" if code else ""
            return (
                '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
                "<title>flowork 设计器插件嵌入示例</title>"
                '<link rel="stylesheet" href="/designer-lib/flowork-designer.css">'
                "<style>body{margin:0}#audit{height:100vh}</style></head><body>"
                '<div id="audit"></div>'
                '<script type="module">'
                "import { mountFloworkDesigner } from '/designer-lib/flowork-designer.es.js';"
                f"mountFloworkDesigner('#audit', {{ apiBase: '', mode: 'readonly'{deep} }});"
                "</script></body></html>"
            )

    @app.exception_handler(FloworkError)
    async def _flowork_error(_request: Any, exc: FloworkError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    # 预演/试算/词汇目录(dry-run / formula/preview / catalog)在 preview_routes
    register_preview_routes(app, engine)
    # 审计数据面（模拟 / 实例只读 / 版本）在 audit_routes（设计器插件计划批 A）
    register_audit_routes(app, engine)
    # 元数据下发面（handler 名录 / 目录摘要）在 meta_routes（设计器插件计划批 D）
    register_meta_routes(app, engine)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
    def get_definition(code: str) -> dict[str, Any]:
        latest = engine.repository.latest_row(code)
        if latest is None:
            raise HTTPException(status_code=404, detail=f"定义 {code} 不存在")
        published_version = _latest_published_version(code)
        if latest["status"] == "draft":
            draft = engine.repository.designer_draft(code)
            assert draft is not None
            # 批 C4 布局分离：草稿存储层 layout 独立成列,响应时合回设计器期望的形状
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

    def _latest_published_version(code: str) -> int | None:
        try:
            return engine.repository.get_definition(code).major
        except FloworkError:
            return None

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
            # 版本判定全貌（批 A3）：破坏性明细在 triggered_rules（breaking 只是布尔）；
            # identical_to 非 None ＝ 内容与该已发布版本相同（幂等去重，未落库）
            "triggered_rules": payload["triggered_rules"],
            "identical_to": payload["identical_to"],
            "status": "published",
        }

    @app.post("/api/validate")
    def validate(body: dict[str, Any], deploy: bool = False) -> dict[str, Any]:
        """设计时校验（不落库）。body 即模板 JSON。

        ``?deploy=true`` 追加部署期校验预演（A11 编辑期前移）：L3 引用完整性 +
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

    if jobs_enabled:

        @app.on_event("startup")
        def _start_jobs() -> None:
            engine.jobs.start(interval_seconds=job_interval)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        engine.jobs.stop()
        engine.close()

    if seed:
        _seed_examples(engine)

    return app


def _seed_examples(engine: ProcessEngine) -> None:
    """空库时把 examples/*.json 灌为草稿（首个示例顺手发布，便于演示两种状态）。"""
    if engine.repository.list_summaries():
        return
    if not EXAMPLES_DIR.is_dir():
        return
    for index, path in enumerate(sorted(EXAMPLES_DIR.glob("*.json"))):
        try:
            definition = load_definition_file(path, engine.config.node_registry)
        except (DefinitionError, json.JSONDecodeError):
            continue
        engine.repository.update_draft(definition)
        if index == 0:
            engine.repository.deploy(definition, published_by="seed")


def __getattr__(name: str) -> Any:
    """``uvicorn flowork.contrib.web.app:app`` 的惰性默认应用。

    import 本模块不再有建引擎/触库的副作用（旧库缺新列时不再炸 import）；
    首次取 ``app`` 属性才按 FLOWORK_DB（缺省 designer.db）创建。
    """
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
