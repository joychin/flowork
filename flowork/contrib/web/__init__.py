"""flowork 官方插件 · Web 传输面：设计器与外部系统的薄投影层。

地位（结构裁定 2026-10-09）：web 是 contrib 插件而非内核包——内核禁止 import
本模块（import-linter「内核不得 import 插件包」自动覆盖），本模块只准经
``flowork.api`` 公开门面使用内核（import-linter「投影/插件只准走公开面」）。

原则：本层不承载业务逻辑 —— 定义解析/校验/发布全部走引擎
（load_definition / repository / validate_definition），路由即
RepositoryService 方法的一一映射（见《命名规范》第 2 节）。
"""

from flowork.contrib.web.app import create_app

__all__ = ["create_app"]

# 兼容声明（openface-plugin-design §4）：依赖的门面契约版本；
# tests/test_plugin_compat.py 断言 ≤ flowork.api.API_VERSION。
REQUIRES_FLOWORK_API = "1"
