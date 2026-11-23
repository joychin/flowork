"""REST /v2 传输面：对外集成面（设计器内部面见 contrib/web，三面互不复用）。

装配两形态：
- ``create_app()``：独立进程——FLOWORK_DB / FLOWORK_ORG / FLOWORK_JOBS 语义同 web；
- ``register_rest_routes(app, engine)``：宿主自带引擎与鉴权时，把 /v2 挂进既有 FastAPI。

启动：``uvicorn flowork.contrib.rest.app:app``（惰性属性，import 无副作用）。
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from flowork.api import FloworkError, ProcessEngine
from flowork.contrib.rest._common import error_payload, status_of
from flowork.contrib.rest.routes_definitions import register_definition_routes
from flowork.contrib.rest.routes_instances import register_instance_routes
from flowork.contrib.rest.routes_system import register_system_routes
from flowork.contrib.rest.routes_tasks import register_task_routes

__all__ = ["create_app", "create_engine", "register_rest_routes"]

logger = logging.getLogger(__name__)


def create_engine(db_file: str | Path | None = None) -> ProcessEngine:
    """按环境约定装配引擎（db / org / FLOWORK_SETUP 全量扩展钩子）。

    ``FLOWORK_SETUP=模块`` 可带三个可选钩子（nodes/vocabulary/handlers）——
    独立部署的 /v2 进程与宿主同样能把自定义函数、handler、节点类型带进来
    （装配单源见 :mod:`flowork.contrib.assembly`）。宿主嵌入形态不必走这里：
    直接 ``register_rest_routes(app, 自己的引擎)``。
    """
    from flowork.contrib.assembly import assemble_engine

    db_path = db_file or os.environ.get("FLOWORK_DB", "flowork.db")
    if db_file is None and "FLOWORK_DB" not in os.environ:
        # 缺省路径落进程工作目录——没有这条提示，数据文件的存在感为零
        logger.warning("未设 FLOWORK_DB，REST 进程使用当前目录下的 flowork.db")
    engine, _source = assemble_engine(str(db_path))
    return engine


def register_rest_routes(app: FastAPI, engine: ProcessEngine) -> None:
    """把 /v2 路由挂进宿主 FastAPI（宿主自带鉴权中间件时用这个入口）。"""

    @app.exception_handler(FloworkError)
    async def _flowork_error(_request: Any, exc: FloworkError) -> JSONResponse:
        return JSONResponse(status_code=status_of(exc), content=error_payload(exc))

    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_request: Any, exc: Any) -> JSONResponse:
        # 路由级 404/405 等原生 {"detail": ...} 形状破坏统一错误体契约——
        # 同样包成 {"error": {...}}，状态码保持语义（不存在/方法不允许）。
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": type(exc).__name__,
                    "message": str(exc.detail),
                    "details": {},
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Any, exc: Exception) -> JSONResponse:
        # 兜底 500：错误体不泄漏内部细节（栈/SQL/路径只进服务端日志）。
        # 无此处理器时未捕获异常返回纯文本 Internal Server Error，
        # 消费方拿不到可解析的错误体。
        logger.exception("未处理异常（%s %s）", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "InternalServerError",
                    "message": "服务器内部错误（详见服务端日志）",
                    "details": {},
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation_error(_request: Any, exc: Any) -> JSONResponse:
        # FastAPI 原生 422 的 {"detail": [...]} 形状破坏统一错误体契约；
        # 转写为 {"error": {...}}，明细进 details.errors
        from fastapi.encoders import jsonable_encoder

        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "RequestValidationError",
                    "message": "请求参数不合法（缺失/类型错，见 details.errors）",
                    "details": {"errors": jsonable_encoder(exc.errors())},
                }
            },
        )

    register_definition_routes(app, engine)
    register_instance_routes(app, engine)
    register_task_routes(app, engine)
    register_system_routes(app, engine)

    @app.get("/v2/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "surface": "v2"}


def create_app(db_file: str | Path | None = None) -> FastAPI:
    """独立进程应用：引擎 + /v2 路由 + 内置调度（FLOWORK_JOBS=off 可关）。"""
    engine = create_engine(db_file)
    app = FastAPI(title="flowork REST API", version="0.1.0")
    app.state.engine = engine
    register_rest_routes(app, engine)

    jobs_enabled = os.environ.get("FLOWORK_JOBS", "on").lower() not in ("off", "0", "false")
    try:
        job_interval = int(os.environ.get("FLOWORK_JOB_INTERVAL", "30"))
    except ValueError as exc:
        raise FloworkError(
            f"FLOWORK_JOB_INTERVAL 须为整数秒数，收到：{os.environ['FLOWORK_JOB_INTERVAL']!r}"
        ) from exc

    @asynccontextmanager
    async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
        if jobs_enabled:
            engine.jobs.start(interval_seconds=job_interval)
        yield
        engine.jobs.stop()
        engine.close()

    app.router.lifespan_context = _lifespan
    return app


def __getattr__(name: str) -> Any:
    """``uvicorn flowork.contrib.rest.app:app`` 的惰性默认应用（import 无副作用）。

    首次取属性创建后写入模块 globals 缓存——同进程重复访问返回同一应用
    （不缓存会每次访问新建引擎，实测属性自比不等）。"""
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"模块属性不存在：{name}")
