"""排练与场景面：交互排练三端点 + 场景 CRUD/运行。

排练＝simulated 实例的人点驱动（步单快照见 engine/rehearsal）；
场景＝steps+controls+assertions 的可复跑脚本（与演练剧本同构，跑同一基座）。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from flowork.api import FloworkError, InstanceNotFoundError, IssueLevel
from flowork.contrib.web._helpers import issues_payload, parse_spec

__all__ = ["register_rehearsal_routes"]


def register_rehearsal_routes(app: Any, engine: Any) -> None:
    """把排练/场景端点挂到应用（engine 为 ProcessEngine）。"""

    @app.post("/api/definitions/{code}/rehearsal")
    def rehearsal_start(
        code: str, body: dict[str, Any], major: int | None = None, revision: int | None = None
    ) -> dict[str, Any]:
        """启动排练（不驱动）：body 可带 spec（草稿直通）/variables/controls。

        模拟前强制设计期校验（与 simulate 同门槛）；返回初始步单。
        """
        from flowork.api import rehearsal_state, start_rehearsal

        spec, variables, controls = _parse_body(engine, code, body, major, revision)
        start_user = body.get("start_user")
        if start_user is not None and not isinstance(start_user, str):
            raise HTTPException(status_code=422, detail="start_user 必须是字符串")
        try:
            instance_id = start_rehearsal(
                engine,
                code,
                variables=variables,
                spec=spec,
                major=major,
                revision=revision,
                controls=controls,
                start_user=start_user or "rehearsal",
            )
        except InstanceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FloworkError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        state = rehearsal_state(engine, instance_id)
        state["instance_id"] = instance_id
        return state

    @app.post("/api/rehearsals/{instance_id}/step")
    def rehearsal_step(instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """执行一步（任务动作或等待兑现），返回新步单。"""
        from flowork.api import perform_rehearsal_step, rehearsal_state

        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="step 必须是对象")
        try:
            perform_rehearsal_step(engine, instance_id, body)
        except InstanceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FloworkError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        state = rehearsal_state(engine, instance_id)
        state["instance_id"] = instance_id
        return state

    @app.get("/api/rehearsals/{instance_id}")
    def rehearsal_detail(instance_id: str) -> dict[str, Any]:
        """步单快照（刷新/恢复排练用）。"""
        from flowork.api import rehearsal_state

        try:
            state = rehearsal_state(engine, instance_id)
        except FloworkError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        state["instance_id"] = instance_id
        return state

    def _parse_body(
        engine: Any, code: str, body: dict[str, Any], major: int | None, revision: int | None
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
        raw_spec = body.get("spec")
        raw_vars = body.get("variables")
        raw_controls = body.get("controls")
        if raw_spec is not None and not isinstance(raw_spec, dict):
            raise HTTPException(status_code=422, detail="spec 必须是流程定义 JSON 对象")
        if raw_vars is not None and not isinstance(raw_vars, dict):
            raise HTTPException(status_code=422, detail="variables 必须是对象")
        if raw_controls is not None and not isinstance(raw_controls, dict):
            raise HTTPException(status_code=422, detail="controls 必须是对象")
        if raw_spec is None and (major is not None or revision is not None):
            return None, raw_vars, raw_controls  # 已发布版本对照（内核按 major/revision 解析）
        from flowork.contrib.web.audit_routes import _saved_draft

        spec = raw_spec if isinstance(raw_spec, dict) else _saved_draft(engine, code)
        definition = parse_spec(engine, spec)
        issues = engine.repository.validate(definition)
        errors = [i for i in issues if i.level == IssueLevel.ERROR]
        if errors:
            raise HTTPException(
                status_code=422,
                detail={"message": "校验未通过，禁止排练", "issues": issues_payload(errors)},
            )
        return spec, raw_vars, raw_controls
