"""flowork Web API：设计器与外部系统的薄传输层。

原则：本层不承载业务逻辑 —— 定义解析/校验/发布全部走引擎
（load_definition / repository / validate_definition），路由即
RepositoryService 方法的一一映射（见《命名规范》第 2 节）。
"""

from flowork.web.app import create_app

__all__ = ["create_app"]
