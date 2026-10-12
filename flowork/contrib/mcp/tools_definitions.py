"""MCP 定义组工具：validate_spec / dry_run / deploy / definition_drift / list_definitions。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from flowork.api import (
    InvalidDefinitionData,
    ProcessEngine,
    load_definition,
    simulate_route,
)
from flowork.contrib.mcp.catalog import ToolSpec

__all__ = ["definition_tools"]


def _load(engine: ProcessEngine, spec: Any) -> Any:
    if not isinstance(spec, dict):
        raise InvalidDefinitionData("spec 必须是流程定义 JSON 对象")
    return load_definition(spec, engine.config.node_registry)


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


def definition_tools(engine: ProcessEngine) -> list[ToolSpec]:
    repository = engine.repository

    def validate_spec(args: dict[str, Any]) -> Any:
        definition = _load(engine, args.get("spec"))
        issues = repository.validate(definition)
        return {
            "ok": not any(issue.level == "error" for issue in issues),
            "issues": [
                {
                    "level": str(issue.level),
                    "rule": issue.rule,
                    "message": issue.message,
                    "node": issue.node,
                    "flow": issue.flow,
                }
                for issue in issues
            ],
        }

    def dry_run(args: dict[str, Any]) -> Any:
        definition = _load(engine, args.get("spec"))
        return _plain(simulate_route(definition, dict(args.get("variables") or {})))

    def deploy(args: dict[str, Any]) -> Any:
        definition = _load(engine, args.get("spec"))
        verdict = repository.deploy(
            definition,
            published_by=str(args.get("actor") or "mcp"),
            target_major=args.get("target_major"),
            force_major_reason=str(args.get("force_major_reason") or ""),
            allow_minor_reason=str(args.get("allow_minor_reason") or ""),
        )
        return {"code": definition.code, **_plain(verdict)}

    def definition_drift(args: dict[str, Any]) -> Any:
        directory = args.get("directory")
        if not directory:
            raise InvalidDefinitionData("directory 必填（定义 JSON 所在目录）")
        return repository.definition_drift(str(directory))

    def list_definitions(args: dict[str, Any]) -> Any:
        return repository.list_definitions(
            code_prefix=str(args.get("code_prefix") or ""),
            latest_only=bool(args.get("latest_only", True)),
        )

    return [
        ToolSpec(
            name="validate_spec",
            description="校验流程定义 spec（设计期静态检查：结构/可达性/CEL 全位点/图级 lint）",
            input_schema={
                "type": "object",
                "properties": {"spec": {"type": "object", "description": "流程定义 JSON"}},
                "required": ["spec"],
            },
            handler=validate_spec,
        ),
        ToolSpec(
            name="dry_run",
            description="按样例变量静态预演路由（不落库；返回步骤/警告/阻断）",
            input_schema={
                "type": "object",
                "properties": {
                    "spec": {"type": "object", "description": "流程定义 JSON"},
                    "variables": {"type": "object", "description": "样例变量"},
                },
                "required": ["spec"],
            },
            handler=dry_run,
        ),
        ToolSpec(
            name="deploy",
            description="部署流程定义（版本判定/幂等去重/破坏性清单；发布即不可变）",
            input_schema={
                "type": "object",
                "properties": {
                    "spec": {"type": "object", "description": "流程定义 JSON"},
                    "actor": {"type": "string", "description": "发布者（裸 id）"},
                    "target_major": {"type": "integer", "description": "维护分支 major"},
                    "force_major_reason": {"type": "string"},
                    "allow_minor_reason": {"type": "string"},
                },
                "required": ["spec"],
            },
            handler=deploy,
        ),
        ToolSpec(
            name="definition_drift",
            description="定义文件目录与库对账（未部署/库多余/库落后三清单；AI/CI 对账）",
            input_schema={
                "type": "object",
                "properties": {"directory": {"type": "string"}},
                "required": ["directory"],
            },
            handler=definition_drift,
        ),
        ToolSpec(
            name="list_definitions",
            description="定义清单（默认每 code 只列最高发布版）",
            input_schema={
                "type": "object",
                "properties": {
                    "code_prefix": {"type": "string"},
                    "latest_only": {"type": "boolean"},
                },
            },
            handler=list_definitions,
        ),
    ]
