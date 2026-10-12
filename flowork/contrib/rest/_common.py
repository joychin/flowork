"""REST /v2 公共件：actor 归因、offset 游标分页、错误状态映射（设计 §2.2–2.4）。"""

from __future__ import annotations

import base64
from dataclasses import asdict, is_dataclass
from typing import Any, Literal, cast

from fastapi import Request
from pydantic import BaseModel

from flowork.api import (
    ConditionError,
    DefinitionError,
    DuplicateStartError,
    FloworkError,
    InvalidDefinition,
    JobStateError,
    NotFoundError,
    TaskNotAvailableError,
)

__all__ = [
    "ACTOR_HEADER",
    "DEFAULT_ACTOR",
    "RestConflictError",
    "RestRequestError",
    "actor_of",
    "dump",
    "encode_offset",
    "error_payload",
    "issue_payload",
    "page_payload",
    "page_request",
    "reuse_of",
    "status_of",
]

ACTOR_HEADER = "X-Flowork-Actor"
DEFAULT_ACTOR = "rest"
MAX_PAGE_LIMIT = 500


class RestRequestError(FloworkError):
    """REST 层请求形状错误（游标不合法、body 缺字段）——422。"""

    status = 422


class RestConflictError(FloworkError):
    """REST 层冲突（signal 回执核验不通过等）——409。"""

    status = 409


def actor_of(request: Request, body: dict[str, Any] | None = None) -> str:
    """操作者归因：body.actor > X-Flowork-Actor 头 > 缺省 rest（鉴权归宿主）。"""
    override = str((body or {}).get("actor") or "")
    if override:
        return override
    return request.headers.get(ACTOR_HEADER) or DEFAULT_ACTOR


def _decode_after(after: str) -> int:
    try:
        raw = base64.b64decode(after, validate=True).decode("utf-8")
        if not raw.startswith("o:"):
            raise ValueError(raw)
        offset = int(raw[2:])
    except Exception as exc:
        raise RestRequestError(f"page.after 游标不合法：{after!r}") from exc
    if offset < 0:
        raise RestRequestError(f"page.after 游标不合法：{after!r}")
    return offset


def page_request(page: dict[str, Any] | None) -> tuple[int, int]:
    """解析 page：返回 (offset, limit)。before 一律 422（offset 游标，不装游标语义）。"""
    page = page or {}
    if page.get("before"):
        raise RestRequestError("page.before 不支持：/v2 分页为 offset 游标，仅 page.after")
    offset = _decode_after(str(page.get("after"))) if page.get("after") else 0
    try:
        limit = int(page.get("limit") or 50)
    except (TypeError, ValueError) as exc:
        raise RestRequestError(f"page.limit 不合法：{page.get('limit')!r}") from exc
    return offset, max(1, min(limit, MAX_PAGE_LIMIT))


def encode_offset(offset: int) -> str:
    """offset → 游标（base64 "o:{offset}"）。"""
    return base64.b64encode(f"o:{offset}".encode()).decode("ascii")


_REUSE_POLICIES = ("reject", "return_existing", "allow_duplicate")


def reuse_of(value: Any) -> Literal["reject", "return_existing", "allow_duplicate"]:
    """启动幂等策略入参校验（非法即 422，不透传裸字符串）。"""
    reuse = str(value or "reject")
    if reuse not in _REUSE_POLICIES:
        raise RestRequestError(f"reuse 不合法：{reuse!r}（可选 {'/'.join(_REUSE_POLICIES)}）")
    return cast(Literal["reject", "return_existing", "allow_duplicate"], reuse)


def page_payload(items: list[Any], offset: int, limit: int) -> dict[str, Any]:
    """切页并生成 next_cursor（仅当本页取满且还有余量）。"""
    window = items[offset : offset + limit]
    has_more = offset + limit < len(items)
    return {"items": window, "next_cursor": encode_offset(offset + limit) if has_more else None}


# isinstance 链优先；类名兜底表覆盖未在 flowork.api 导出的错误家族（设计 §2.3：
# 不为错误映射扩公开面）。
_STATUS_BY_NAME: dict[str, int] = {
    "LockConflictError": 409,
    "ParticipantCountMismatchError": 422,
    "ParticipantLimitError": 422,
    "FormulaError": 422,
    "RestRequestError": 422,
    "RestConflictError": 409,
}


def status_of(exc: FloworkError) -> int:
    """FloworkError → HTTP 状态码。"""
    declared = getattr(exc, "status", None)
    if isinstance(declared, int):
        return declared
    if isinstance(exc, NotFoundError):
        return 404
    if isinstance(exc, (TaskNotAvailableError, DuplicateStartError, JobStateError)):
        return 409
    if isinstance(exc, (DefinitionError, ConditionError)):
        return 422
    return _STATUS_BY_NAME.get(type(exc).__name__, 500)


def issue_payload(exc: InvalidDefinition) -> dict[str, Any]:
    """设计期校验失败 → 422 details（issues 逐条带规则编码与定位）。"""
    return {
        "issues": [
            {
                "level": str(issue.level),
                "rule": issue.rule,
                "message": issue.message,
                "node": issue.node,
                "flow": issue.flow,
            }
            for issue in exc.issues
        ]
    }


def error_payload(exc: FloworkError) -> dict[str, Any]:
    """统一错误体 {"error": {code, message, details}}（spec §12）。"""
    details: dict[str, Any] = {}
    if isinstance(exc, InvalidDefinition):
        details.update(issue_payload(exc))
    return {"error": {"code": type(exc).__name__, "message": str(exc), "details": details}}


def dump(value: Any) -> Any:
    """出参归一：pydantic/dataclass → json 化 dict；其余交 FastAPI 编码器。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value
