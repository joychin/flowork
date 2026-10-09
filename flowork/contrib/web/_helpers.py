"""web 传输层共用小件：错误 payloads 与 Spec 解析（app.py 拆分守 300 行红线）。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from flowork.api import (
    DefinitionError,
    InvalidDefinitionData,
    ProcessDefinition,
    UnknownNodeKind,
    ValidateIssue,
    load_definition,
)
from flowork.api import ProcessEngine as _ProcessEngine  # 仅类型用途

__all__ = ["issues_payload", "parse_spec"]


def issues_payload(issues: list[ValidateIssue]) -> list[dict[str, Any]]:
    return [
        {
            "level": issue.level.value,
            "rule": issue.rule,
            "message": issue.message,
            "node": issue.node,
            "flow": issue.flow,
        }
        for issue in issues
    ]


def parse_spec(engine: _ProcessEngine, data: Any) -> ProcessDefinition:
    """把请求体解析为定义（未知 kind / 非法结构 → 422）。"""
    try:
        return load_definition(data, engine.config.node_registry)
    except InvalidDefinitionData as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnknownNodeKind as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
