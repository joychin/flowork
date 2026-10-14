"""REST /v2 传输面插件（对外集成面；设计器内部面见 contrib/web）。

插件四件套：目录结构/
契约快照/公开面扩展点/兼容声明。全部内核访问经 flowork.api——由
tests/test_projection_surface.py 静态纪律与 import-linter 反向契约强制。
"""

from __future__ import annotations

from flowork.contrib.rest.app import create_app, create_engine, register_rest_routes

__all__ = ["create_app", "create_engine", "register_rest_routes"]

# 兼容声明：本插件依赖的门面契约版本；测试断言 ≤ flowork.api.API_VERSION。
REQUIRES_FLOWORK_API = "1"
