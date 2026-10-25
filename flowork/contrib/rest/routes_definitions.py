"""REST /v2 definitions 面：search / deploy / get / retire（purge 仅 CLI）。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from flowork.api import ProcessEngine, load_definition
from flowork.contrib.rest._common import (
    RestRequestError,
    actor_of,
    dict_of,
    dump,
    page_payload,
    page_request,
)

__all__ = ["register_definition_routes"]


def register_definition_routes(app: FastAPI, engine: ProcessEngine) -> None:
    repository = engine.repository

    @app.post("/v2/definitions/search")
    def definitions_search(body: dict[str, Any]) -> dict[str, Any]:
        flt = dict_of(body.get("filter"), "filter")
        rows = repository.list_definitions(
            code_prefix=str(flt.get("code_prefix") or ""),
            latest_only=bool(flt.get("latest_only", True)),
        )
        status = flt.get("status")
        if status:
            rows = [row for row in rows if row.get("status") == status]
        offset, limit = page_request(body.get("page"))
        return page_payload(rows, offset, limit)

    @app.post("/v2/definitions/deploy")
    def deploy(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        spec = body.get("spec")
        if not isinstance(spec, dict):
            raise RestRequestError("body.spec 必须是流程定义 JSON 对象")
        definition = load_definition(spec, engine.config.node_registry)
        verdict = repository.deploy(
            definition,
            published_by=actor_of(request, body),
            target_major=body.get("target_major"),
            force_major_reason=str(body.get("force_major_reason") or ""),
            allow_minor_reason=str(body.get("allow_minor_reason") or ""),
        )
        return {"code": definition.code, **dump(verdict)}

    @app.get("/v2/definitions/{code}")
    def get_definition(code: str, major: int | None = None, revision: int | None = None) -> Any:
        return repository.get_definition_json(code, major=major, revision=revision)

    @app.post("/v2/definitions/{code}/retire")
    def retire(request: Request, code: str, body: dict[str, Any]) -> dict[str, Any]:
        retired = repository.retire(
            code, actor=actor_of(request, body), reason=str(body.get("reason") or "")
        )
        return {"code": code, "retired": retired}
