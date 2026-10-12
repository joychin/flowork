"""REST /v2 系统面：messages / jobs（含长轮询）/ incidents / events（spec §12）。"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI

from flowork.api import JobClaim, ProcessEngine
from flowork.contrib.rest._common import (
    RestRequestError,
    dump,
    encode_offset,
    page_payload,
    page_request,
)

__all__ = ["register_system_routes"]

_MAX_POLL_SECONDS = 30.0


def _claim_json(claim: JobClaim) -> dict[str, Any]:
    return {
        "job_id": claim.job_id,
        "job_type": claim.job_type,
        "payload": claim.payload,
        "max_retries": claim.max_retries,
    }


def register_system_routes(app: FastAPI, engine: ProcessEngine) -> None:
    messages = engine.messages
    worker_jobs = engine.worker_jobs
    runtime = engine.runtime
    history = engine.history

    @app.post("/v2/messages/publish")
    def publish(body: dict[str, Any]) -> dict[str, Any]:
        name = body.get("name")
        if not name:
            raise RestRequestError("body.name 必填")
        optional: dict[str, Any] = {}
        if body.get("message_id"):
            optional["message_id"] = body["message_id"]
        if body.get("ttl_seconds") is not None:
            optional["ttl_seconds"] = body["ttl_seconds"]
        matched = messages.publish_message(
            str(name),
            correlation_key=str(body.get("correlation_key") or ""),
            payload=body.get("payload"),
            **optional,
        )
        return {"name": name, "matched": matched}

    @app.post("/v2/jobs/activate")
    def activate(body: dict[str, Any]) -> dict[str, Any]:
        """长轮询领活：request_timeout 秒内无活则轮询等待（上限 30s，0=立即返回）。"""
        job_types = body.get("job_types")
        if not isinstance(job_types, list) or not job_types:
            raise RestRequestError("body.job_types 必填（非空列表）")
        worker_id = body.get("worker_id")
        if not worker_id:
            raise RestRequestError("body.worker_id 必填")
        try:
            timeout = min(float(body.get("request_timeout") or 0.0), _MAX_POLL_SECONDS)
        except (TypeError, ValueError) as exc:
            raise RestRequestError(
                f"request_timeout 不合法：{body.get('request_timeout')!r}"
            ) from exc
        deadline = time.monotonic() + timeout
        while True:
            claims = worker_jobs.activate_jobs(
                [str(t) for t in job_types],
                worker_id=str(worker_id),
                max=int(body.get("max") or 10),
                visibility_timeout_seconds=int(body.get("visibility_timeout_seconds") or 300),
            )
            if claims or time.monotonic() >= deadline:
                return {"jobs": [_claim_json(claim) for claim in claims]}
            time.sleep(0.5)

    @app.post("/v2/jobs/{job_id}/complete")
    def complete_job(job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        worker_jobs.complete_job(
            job_id, worker_id=str(body.get("worker_id") or ""), output=body.get("output")
        )
        return {"job_id": job_id, "state": "done"}

    @app.post("/v2/jobs/{job_id}/fail")
    def fail_job(job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        state = worker_jobs.fail_job(
            job_id,
            worker_id=str(body.get("worker_id") or ""),
            error=str(body.get("error") or ""),
            retries=body.get("retries"),
            backoff_seconds=body.get("backoff_seconds"),
        )
        return {"job_id": job_id, "state": state}

    @app.post("/v2/jobs/search")
    def jobs_search(body: dict[str, Any]) -> dict[str, Any]:
        flt = body.get("filter") or {}
        offset, limit = page_request(body.get("page"))
        rows = engine.worker_jobs.list_jobs(
            state=flt.get("state"),
            job_type=flt.get("job_type"),
            instance_id=flt.get("instance_id"),
            offset=offset,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        return {
            "items": rows[:limit],
            "next_cursor": encode_offset(offset + limit) if has_more else None,
        }

    @app.post("/v2/incidents/search")
    def incidents_search(body: dict[str, Any]) -> dict[str, Any]:
        flt = body.get("filter") or {}
        rows = runtime.list_incidents(flt.get("instance_id"))
        offset, limit = page_request(body.get("page"))
        return page_payload(rows, offset, limit)

    @app.post("/v2/events/search")
    def events_search(body: dict[str, Any]) -> dict[str, Any]:
        flt = body.get("filter") or {}
        offset, limit = page_request(body.get("page"))
        rows = history.events(flt.get("instance_id"), limit=min(offset + limit, 1000))
        return page_payload([dump(row) for row in rows], offset, limit)
