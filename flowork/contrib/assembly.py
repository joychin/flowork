"""官方传输面（web/rest）共用的进程装配：一个模块把宿主扩展全部带进独立进程。

**问题**（2026-10-18 插件体系走查定案）：扩展点都挂在引擎上，而独立进程的每个
面各自从环境变量装配自己的引擎——每个扩展点 × 每个面 = 一座要单独修的桥，修桥
顺序跟着踩坑走（先是组织目录，再是表达式函数），handler 与自定义节点类型则
根本没有桥。本模块把桥一次修完：

``FLOWORK_SETUP=myapp.designer_setup`` —— 宿主提供**一个**模块，可选暴露三个
钩子（缺哪个跳哪个，全部可选）：

- ``nodes(registry) -> None``：注册自定义节点类型（引擎构建**前**调用；
  ``registry.register(kind, spec, behavior, ui=...)``）；
- ``vocabulary(org) -> ExpressionFunctions | None``：宿主自定义表达式函数；
- ``handlers(engine) -> None``：注册 service_task 动作与钩子 handler
  （引擎构建**后**调用；``engine.register_handler(name, fn)``）。

词汇优先级：显式参数 > ``setup.vocabulary`` > ``FLOWORK_VOCABULARY``
（便捷子集，仅函数）> 配了组织目录时 ``contrib.cel_vocabulary(org)``。

设计边界：不在这里做「悄悄补标准词汇」——生产没注册的函数独立进程也不装，
否则「设计器绿、线上挂」。装配结果必须在 ``/api/runtime`` 可见（函数名清单），
不静默。
"""

from __future__ import annotations

import importlib
import os
from typing import Any

from flowork.api import (
    EngineConfig,
    ExpressionFunctions,
    FloworkError,
    ProcessEngine,
    StaticOrgDirectory,
    create_registry,
)

__all__ = ["SETUP_ENV", "assemble_engine"]

SETUP_ENV = "FLOWORK_SETUP"


def assemble_engine(
    db_file: str | None = None,
    *,
    vocabulary: ExpressionFunctions | None = None,
) -> tuple[ProcessEngine, str]:
    """按环境约定装配引擎（web 与 rest 共用）。返回 (engine, 词汇来源说明)。

    ``vocabulary`` 显式参数优先级最高（宿主嵌入形态直接传代码对象）；
    来源说明进 ``/api/runtime``——前端据此显示当前进程认识哪些函数。
    """
    if db_file is not None and not str(db_file).strip():
        # 空串是误配置（如 FLOWORK_DB= 置空）——静默落 :memory: 会让生产
        # 进程重启丢库（2026-10-21 内核 review P2）
        raise FloworkError("db_file 不能为空串（FLOWORK_DB 置空＝误配置，不落内存库）")

    org: Any = None
    org_path = os.environ.get("FLOWORK_ORG")
    if org_path:
        org = StaticOrgDirectory.from_yaml(org_path)

    setup = _load_setup_module()

    node_registry = create_registry()
    if setup is not None:
        _call_optional(setup, "nodes", node_registry)

    config_kwargs: dict[str, Any] = {
        "db_file": db_file or ":memory:",
        "node_registry": node_registry,
    }
    if org is not None:
        config_kwargs["org"] = org

    functions_port, source = _resolve_vocabulary(org, setup, explicit=vocabulary)
    if functions_port is not None:
        config_kwargs["expression_functions"] = functions_port

    engine = ProcessEngine(EngineConfig(**config_kwargs))

    if setup is not None:
        _call_optional(setup, "handlers", engine)

    return engine, source


def _resolve_vocabulary(
    org: Any, setup: Any | None, *, explicit: ExpressionFunctions | None
) -> tuple[ExpressionFunctions | None, str]:
    """词汇来源四级优先：显式 > setup 钩子 > FLOWORK_VOCABULARY > org 默认词汇。"""
    from flowork.contrib.web.vocabulary import load_vocabulary

    if explicit is not None:
        return load_vocabulary(org, explicit=explicit)
    if setup is not None:
        factory = getattr(setup, "vocabulary", None)
        if callable(factory):
            candidate = factory(org)
            if candidate is not None:
                if not callable(getattr(candidate, "functions", None)):
                    raise FloworkError(
                        f"{SETUP_ENV} 的 vocabulary() 须返回 ExpressionFunctions"
                        f"（有 functions() 方法），返回了 {type(candidate).__name__}"
                    )
                return candidate, f"{SETUP_ENV}={os.environ.get(SETUP_ENV) or ''}"
    return load_vocabulary(org)


def _call_optional(module: Any, hook: str, *args: Any) -> None:
    """调用模块的可选钩子；钩子不存在就跳过，存在但不可调用是装配错误。"""
    fn = getattr(module, hook, None)
    if fn is None:
        return
    if not callable(fn):
        raise FloworkError(f"{SETUP_ENV} 的 {hook} 钩子必须可调用")
    fn(*args)


def _load_setup_module() -> Any | None:
    spec = (os.environ.get(SETUP_ENV) or "").strip()
    if not spec:
        return None
    try:
        return importlib.import_module(spec)
    except ImportError as exc:
        raise FloworkError(f"{SETUP_ENV} 指向的模块导入失败：{spec}（{exc}）") from exc
