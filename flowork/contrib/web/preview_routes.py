"""设计器预演/试算/词汇下发面：dry-run / 公式试算 / 表达式词汇目录。

``register_preview_routes(app, engine)`` 把三个
端点挂到既有应用上，无独立状态。词汇目录与 validator 同源（同一份
``expr/catalog``）——设计器公式编辑器的点选/提示不再手写第二份词汇。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from flowork.api import (
    FloworkError,
    evaluate_formula_value,
    expression_functions_of,
    simulate_route,
    utc_now,
)
from flowork.contrib.web._helpers import parse_spec

__all__ = ["register_preview_routes"]


def register_preview_routes(app: Any, engine: Any) -> None:
    """把 dry-run / 公式试算 / 词汇目录挂到应用（engine 为 ProcessEngine）。"""

    @app.post("/api/dry-run")
    def dry_run(body: dict[str, Any]) -> dict[str, Any]:
        """设计时路由预演（不落库、不执行节点行为）：按样例变量静态模拟条件路由。

        body：``{"spec": 定义 JSON, "variables": {样例}, "assumptions":
        {等待节点 code: "matched"|"elapsed"|"auto"}}``。假设缺省 auto＝
        消息到达 / 超时两面都预演（先到者决定走向，预演两面都画出来）。
        """
        definition = parse_spec(engine, body.get("spec"))
        raw_vars = body.get("variables")
        variables = raw_vars if isinstance(raw_vars, dict) else {}
        raw_assumptions = body.get("assumptions")
        assumptions = raw_assumptions if isinstance(raw_assumptions, dict) else None
        result = simulate_route(
            definition,
            variables,
            functions=expression_functions_of(engine.config),
            assumptions=assumptions,
        )
        return {
            "ok": result.ok,
            "steps": [
                {
                    "node": step.node,
                    "via_source": step.via_source,
                    "via_reason": step.via_reason,
                    "note": step.note,
                }
                for step in result.steps
            ],
            "warnings": result.warnings,
            "blocked": result.blocked,
        }

    @app.post("/api/formula/preview")
    def formula_preview(body: dict[str, Any]) -> dict[str, Any]:
        """公式试算：CEL 对样例变量求值（含引擎配置的自定义函数），原样返回值或错误说明。

        值语义（位点统一）：条件公式返回 bool，取人/映射公式返回 str/list/数值；
        样例缺 initiator 时注入占位 ``u_initiator``，缺 now 时注入统一时钟，
        缺 instance 时注入占位形状——试算可引用保留标识符，与运行时同一注入面。
        """
        formula = str(body.get("formula") or "")
        raw_vars = body.get("variables")
        variables = dict(raw_vars) if isinstance(raw_vars, dict) else {}
        variables.setdefault("initiator", "u_initiator")
        variables.setdefault("now", utc_now())
        variables.setdefault(
            "instance", {"id": "i_preview", "started_at": utc_now(), "business_key": None}
        )
        try:
            return {
                "ok": True,
                "result": evaluate_formula_value(
                    formula, variables, functions=expression_functions_of(engine.config)
                ),
            }
        except FloworkError as exc:
            return {"ok": False, "error": str(exc)}

    @app.get("/api/catalog")
    def catalog() -> dict[str, Any]:
        """表达式词汇目录：保留词 + 引擎注入函数。

        每条带类型/中文标签/可用位点/备注；前端公式编辑器按位点过滤后点选插入，
        与 validator 静态检查同一份权威（expr/catalog）。宿主自定义函数（SPI 注册）
        的签名下发随 REST /v2 批一并做——本端点只覆盖引擎侧权威目录。
        """
        from flowork.api import engine_functions, identifier_entries

        return {
            "identifiers": [
                {**asdict(entry), "positions": sorted(entry.positions)}
                for entry in identifier_entries()
            ],
            "functions": [
                {**asdict(entry), "positions": sorted(entry.positions)}
                for entry in engine_functions().values()
            ],
        }
