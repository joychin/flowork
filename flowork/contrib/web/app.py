"""FastAPI 应用工厂：组装各面路由 + 运行形态选择（会话内临时库 / 文件事实源）。

设计器**不碰用户的运行时数据库**——它自己的引擎是一份进程内临时库（内存 SQLite），
只承载本次会话的模拟/场景执行痕迹，进程退出即消失。定义的事实源是 JSON 文件
（``FLOWORK_DEFINITIONS_DIR``）或浏览器里打开的本地文件。

两种部署形态::

    # 独立进程（开发者本机）：
    uv run uvicorn flowork.contrib.web.app:app --port 8901
    FLOWORK_DEFINITIONS_DIR=./examples FLOWORK_ORG=./examples/org.yaml \
        uv run uvicorn flowork.contrib.web.app:app

    # 宿主挂载（自己装配好引擎——扩展点全跟引擎走）：
    from flowork.contrib.web.app import register_designer_routes
    register_designer_routes(app, engine, definitions_dir=None)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from flowork.api import ExpressionFunctions, FloworkError, ProcessEngine
from flowork.contrib.web.audit_routes import register_audit_routes
from flowork.contrib.web.definition_routes import FORM_EXCLUDE as FORM_EXCLUDE
from flowork.contrib.web.definition_routes import register_definition_routes
from flowork.contrib.web.file_routes import register_file_routes, register_runtime_routes
from flowork.contrib.web.meta_routes import register_meta_routes
from flowork.contrib.web.preview_routes import register_preview_routes
from flowork.contrib.web.scenario_routes import register_scenario_routes
from flowork.contrib.web.vocabulary import vocabulary_functions

# "app" 为惰性属性(模块 __getattr__ 按需创建,import 无副作用),不在 __all__
__all__ = ["create_app", "register_designer_routes"]

DEFAULT_DB = ":memory:"

_MEMORY_DB = (":memory:", "sqlite:///:memory:")


def register_designer_routes(
    app: FastAPI,
    engine: ProcessEngine,
    *,
    definitions_dir: str | Path | None = None,
    vocabulary_source: str | None = None,
) -> None:
    """把设计器 /api 面挂进宿主 FastAPI（与 REST 的 register_rest_routes 对称）。

    宿主把自己装配好的引擎递进来——自定义函数、handler、节点类型全跟引擎来，
    不需要在本函数再配任何东西（「扩展跟着引擎走，不跟着面走」）。

    三个边界，宿主须知：

    - **模拟实例写宿主库**：设计器的模拟跑在传入引擎上，simulated 实例行落
      该引擎的库（查询面默认隔离它们）。不想要这行为，就传一个内存引擎进来。
    - **CORS / 鉴权 / 生命周期不归这里**：宿主应用自己加中间件、自己管理
      ``engine`` 的关闭与 Job 调度。
    - ``definitions_dir`` 给了才挂文件面（读写该目录里的 *.json）；不给则
      定义面走引擎库（宿主库里有定义就能打开）。
    """

    @app.exception_handler(FloworkError)
    async def _flowork_error(_request: Any, exc: FloworkError) -> JSONResponse:
        # NotFoundError 族 404：修前一律 422，与 REST /v2 面同错误
        # 不同状态码——同库同引擎两面消费方要维护两套语义
        from flowork.api import NotFoundError

        status = 404 if isinstance(exc, NotFoundError) else 422
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    app.state.engine = engine
    root = str(Path(definitions_dir).expanduser().resolve()) if definitions_dir else None
    app.state.definitions_dir = root
    # 词汇可见性：装配了什么就报什么（宿主引擎自带 vs 各环境来源）
    port = engine.config.expression_functions
    app.state.vocabulary_source = vocabulary_source or (
        "宿主引擎自带" if port is not None else "未装配（自定义函数不可用）"
    )
    app.state.vocabulary_functions = vocabulary_functions(port)
    session_db = str(engine.config.db_file or "")
    app.state.db_url = session_db

    register_definition_routes(app, engine)
    register_preview_routes(app, engine)
    register_audit_routes(app, engine)
    # 场景脚本随定义文件同住定义目录（<code>.scenario.json）；无目录＝无写面
    register_scenario_routes(app, engine, root)
    register_meta_routes(app, engine)
    register_runtime_routes(
        app,
        root,
        session_db,
        vocabulary_source=app.state.vocabulary_source,
        vocabulary_functions=app.state.vocabulary_functions,
    )
    if root:
        register_file_routes(app, engine, root)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}


def create_app(
    db_file: str | Path | None = None,
    *,
    definitions_dir: str | Path | None = None,
    vocabulary: ExpressionFunctions | None = None,
) -> FastAPI:
    """创建独立进程应用。

    **库隔离（本设计器的硬约束）**：引擎缺省用进程内内存库，进程退出即丢；
    ``FLOWORK_DB``（用户运行时库）在这里被**有意忽略**——设计器从不读写它。
    需要持久化本次会话的模拟痕迹时，显式给 ``db_file`` 或 ``FLOWORK_DESIGNER_DB``
    指向一个专用的临时库（与生产库物理分开）。

    超时 Job 调度缺省关闭：临时库里没有需要推进的 Job，开着一个 worker 线程
    只会空转。``FLOWORK_JOBS=on`` 可显式开启；``FLOWORK_JOB_INTERVAL`` 为轮询秒数。

    ``definitions_dir``（或环境变量 ``FLOWORK_DEFINITIONS_DIR``）：文件事实源——
    给了就以该目录为定义列表与保存目标（git 工作区里的 *.json）。

    ``vocabulary``（或 ``FLOWORK_SETUP`` / ``FLOWORK_VOCABULARY``）：宿主的自定义
    表达式函数、handler、节点类型经 :mod:`flowork.contrib.assembly` 统一带进本进程。
    **不装配，任何用了自定义取人函数的流程在设计器里都跑不起来**（求值报未声明
    函数）。配了组织目录时缺省装 ``contrib.cel_vocabulary``——与 contrib 文档里
    给宿主的写法一致；其余情况要求显式给，不偷偷补一套。
    """
    # FLOWORK_DB 是宿主运行时的库；设计器有意不读它。设了就是误用——
    # 明说一句，别让人以为自己的运行库被设计器碰了。
    if os.environ.get("FLOWORK_DB"):
        print(
            "flowork designer: 忽略 FLOWORK_DB（设计器不读写宿主运行时数据库）——"
            "会话痕迹用 FLOWORK_DESIGNER_DB 指定专用临时库，缺省为内存",
            file=sys.stderr,
        )
    db_path = str(db_file or os.environ.get("FLOWORK_DESIGNER_DB") or DEFAULT_DB)
    # 装配单源：org / FLOWORK_SETUP（nodes+vocabulary+handlers）/ 词汇四级优先
    from flowork.contrib.assembly import assemble_engine

    engine, vocabulary_source = assemble_engine(db_path, vocabulary=vocabulary)

    app = FastAPI(title="flowork designer API", version="0.1.0")
    files_dir = definitions_dir or os.environ.get("FLOWORK_DEFINITIONS_DIR") or None
    register_designer_routes(
        app, engine, definitions_dir=files_dir, vocabulary_source=vocabulary_source
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 插件库托管（单进程 demo 模式）：designer/dist-lib 存在时挂载——
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
                "<style>body{margin:0}#audit{height:100vh}</style></body><body>"
                '<div id="audit"></div>'
                '<script type="module">'
                "import { mountFloworkDesigner } from '/designer-lib/flowork-designer.es.js';"
                f"mountFloworkDesigner('#audit', {{ apiBase: '', mode: 'readonly'{deep} }});"
                "</script></body></html>"
            )

    jobs_enabled = os.environ.get("FLOWORK_JOBS", "off").lower() not in ("off", "0", "false")
    job_interval = int(os.environ.get("FLOWORK_JOB_INTERVAL", "30"))
    if jobs_enabled:

        @app.on_event("startup")
        def _start_jobs() -> None:
            engine.jobs.start(interval_seconds=job_interval)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        engine.jobs.stop()
        engine.close()

    return app


def __getattr__(name: str) -> Any:
    """``uvicorn flowork.contrib.web.app:app`` 的惰性默认应用。

    import 本模块无建引擎/触库副作用；首次取 ``app`` 属性才创建——
    缺省是进程内临时库（不读写 ``FLOWORK_DB``）。
    """
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
