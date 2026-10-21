"""设计器进程的表达式函数装配：让「用户自定义的取人函数」在设计器里能跑。

问题：设计器是**独立进程**，宿主的自定义 CEL 函数住在宿主的 Python 代码里
（``EngineConfig.expression_functions``）。不把它带进设计器进程，任何用了
``manager_of`` / 自定义取人函数的流程连模拟都跑不起来——求值直接报未声明函数。

三条来源，按优先级：

1. **显式传入** ``create_app(vocabulary=...)``——宿主嵌入设计器时最直接；
2. **环境变量** ``FLOWORK_VOCABULARY=myapp.vocab:build``——独立进程形态，
   模块路径 + 工厂函数名（工厂返回 ``ExpressionFunctions``）；
3. **自动兜底**：配了组织目录（``FLOWORK_ORG``）时装 ``contrib.cel_vocabulary(org)``
   ——那是 contrib 文档里给宿主的标准写法，设计器跟着装才与生产一致。

不做「悄悄补一套标准词汇」这种好事：若生产没注册某个函数，设计器却注册了，
就会出现「设计器绿、线上挂」——比跑不起来更糟。所以除了 org 词汇这个
文档化的默认，其余一律要求显式给。
"""

from __future__ import annotations

import importlib
from typing import Any, cast

from flowork.api import ExpressionFunctions, FloworkError

__all__ = ["load_vocabulary", "vocabulary_functions"]

VOCABULARY_ENV = "FLOWORK_VOCABULARY"


def load_vocabulary(
    org: Any = None, explicit: ExpressionFunctions | None = None
) -> tuple[ExpressionFunctions | None, str]:
    """解析设计器要用的表达式函数来源。返回 ``(port, 说明)``。

    说明进 ``/api/runtime``，前端据此告诉用户「这个设计器认识哪些函数」——
    不静默：用户必须能看出当前进程认不认他的自定义函数。
    """
    import os

    if explicit is not None:
        _require_port(explicit, "create_app(vocabulary=...)")
        return explicit, "create_app"

    spec = (os.environ.get(VOCABULARY_ENV) or "").strip()
    if spec:
        return _from_spec(spec), f"{VOCABULARY_ENV}={spec}"

    if org is not None:
        from flowork import contrib

        return contrib.cel_vocabulary(org), "组织目录默认词汇（cel_vocabulary）"

    return None, "未装配（自定义函数不可用）"


def _from_spec(spec: str) -> ExpressionFunctions:
    """``module:factory`` → 调用工厂取端口。"""
    module_name, _, factory_name = spec.partition(":")
    if not module_name or not factory_name:
        raise FloworkError(
            f"{VOCABULARY_ENV} 须为 '模块路径:工厂函数名'（如 myapp.vocab:build），收到：{spec!r}"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise FloworkError(f"{VOCABULARY_ENV} 指向的模块导入失败：{module_name}（{exc}）") from exc
    factory = getattr(module, factory_name, None)
    if not callable(factory):
        raise FloworkError(f"{VOCABULARY_ENV}：模块 {module_name} 里没有可调用的 {factory_name}")
    port = factory()
    _require_port(port, f"{module_name}:{factory_name}()")
    return cast("ExpressionFunctions", port)


def _require_port(candidate: Any, where: str) -> None:
    if not callable(getattr(candidate, "functions", None)):
        raise FloworkError(
            f"表达式函数来源须实现 ExpressionFunctions 端口（有 functions() 方法）：{where} "
            f"返回了 {type(candidate).__name__}"
        )


def vocabulary_functions(port: ExpressionFunctions | None) -> list[str]:
    """端口里的函数名（升序），供前端展示与排障。"""
    if port is None:
        return []
    return sorted(port.functions())
