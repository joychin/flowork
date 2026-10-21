"""web 传输层共用小件：错误 payloads 与 Spec 解析（app.py 拆分守 300 行红线）。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

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
    """把请求体解析为定义（未知 kind / 非法结构 → 422）。

    节点字段类型错会在 ``_resolve_node`` 里抛 pydantic ``ValidationError``
    （它继承 ValueError，且不经 load_definition 的包装路径）——不接住就是 500，
    客户端拿到「服务器内部错误」而非「你的定义哪里写错了」。
    """
    try:
        return load_definition(data, engine.config.node_registry)
    except InvalidDefinitionData as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnknownNodeKind as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"定义结构不合法：{exc}") from exc
    except DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
