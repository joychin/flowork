"""设计器元数据下发面（设计器插件计划批 D）：handler 名录 / 组织目录只读摘要。

服务任务的动作名与取人引用（role:/org:/用户）此前只能凭空填——名录下发后
前端下拉辅助把「未注册/拼错人」消灭在选择时。目录读 engine.config.org
（duck-typing 探测静态目录的 users/departments 字段，SPI 四方法不动）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["register_meta_routes"]


def register_meta_routes(app: Any, engine: Any) -> None:
    """把 handler 名录与目录摘要挂到应用（engine 为 ProcessEngine）。"""

    @app.get("/api/handlers")
    def handlers() -> dict[str, Any]:
        """已注册 handler 名录（D2）：service 节点动作下拉的数据源。"""
        return {"handlers": sorted(engine.handlers.names())}

    @app.get("/api/directory/summary")
    def directory_summary() -> dict[str, Any]:
        """组织目录只读摘要（D1）：用户/部门/角色——取人引用翻译与下拉辅助。

        未配置目录（engine.config.org 为空或非静态目录）返回 configured=false，
        前端按无目录降级（组引用保持原样，不做翻译）。
        """
        org = engine.config.org
        users = getattr(org, "users", None)
        if not isinstance(users, dict):
            return {"configured": False, "users": [], "departments": [], "roles": []}
        departments = getattr(org, "departments", None)
        department_items: list[dict[str, Any]] = []
        if isinstance(departments, dict):
            for code, info in sorted(departments.items()):
                info = info if isinstance(info, dict) else {}
                department_items.append(
                    {
                        "code": code,
                        "name": info.get("name") or code,
                        "parent": info.get("parent"),
                    }
                )
        roles = sorted(
            {
                role
                for info in users.values()
                if isinstance(info, dict)
                for role in (info.get("roles") or [])
            }
        )
        return {
            "configured": True,
            "users": [
                {
                    "id": user_id,
                    "department": (info.get("department") if isinstance(info, dict) else None),
                    "roles": list(info.get("roles") or []) if isinstance(info, dict) else [],
                    "manager": (info.get("manager") if isinstance(info, dict) else None),
                }
                for user_id, info in sorted(users.items())
            ],
            "departments": department_items,
            "roles": roles,
        }
