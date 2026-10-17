"""排练与场景面：交互排练三端点 + 场景 CRUD/运行。

排练＝simulated 实例的人点驱动（步单快照见 engine/rehearsal）；
场景＝steps+controls+assertions 的可复跑脚本（与演练剧本同构，跑同一基座）。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from flowork.api import FloworkError, IssueLevel
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

    # —— 场景（script + assertions；设计器审阅资产）——

    @app.get("/api/definitions/{code}/scenarios")
    def list_scenarios(code: str) -> list[dict[str, Any]]:
        """场景列表（带最近一次运行红绿）。"""
        from flowork.api import scenario_store

        return scenario_store(engine).list(code)

    @app.post("/api/definitions/{code}/scenarios")
    def save_scenario(code: str, body: dict[str, Any]) -> dict[str, Any]:
        """新建/更新场景：body {name, script, description?, id?}。"""
        from flowork.api import parse_scenario, scenario_store

        if not isinstance(body, dict) or not isinstance(body.get("script"), dict):
            raise HTTPException(status_code=422, detail="body 须含 script 对象")
        try:
            parse_scenario(body["script"])
            return scenario_store(engine).save(
                code,
                name=str(body.get("name") or ""),
                script=body["script"],
                description=str(body.get("description") or ""),
                scenario_id=body.get("id"),
            )
        except FloworkError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/scenarios/{scenario_id}")
    def delete_scenario(scenario_id: str) -> dict[str, Any]:
        """删除场景。"""
        from flowork.api import scenario_store

        if not scenario_store(engine).delete(scenario_id):
            raise HTTPException(status_code=404, detail="场景不存在")
        return {"deleted": scenario_id}

    @app.get("/api/scenarios/{scenario_id}")
    def get_scenario(scenario_id: str) -> dict[str, Any]:
        """取场景（含完整脚本）。"""
        from flowork.api import scenario_store

        row = scenario_store(engine).get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="场景不存在")
        return row

    @app.post("/api/scenarios/{scenario_id}/run")
    def run_stored_scenario(scenario_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """运行已存场景（body 可给 spec 覆盖——当前草稿含未保存编辑）。"""
        from dataclasses import asdict

        from flowork.api import scenario_store

        row = scenario_store(engine).get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="场景不存在")
        raw = body or {}
        raw_spec = raw.get("spec") if isinstance(raw, dict) else None
        if raw_spec is not None and not isinstance(raw_spec, dict):
            raise HTTPException(status_code=422, detail="spec 必须是流程定义 JSON 对象")
        report = _run(engine, row["definition_code"], row["script"], raw_spec)
        scenario_store(engine).record_run(scenario_id, ok=report.ok, summary=_summary_text(report))
        return asdict(report)

    @app.post("/api/definitions/{code}/scenarios/run")
    def run_inline_scenario(code: str, body: dict[str, Any]) -> dict[str, Any]:
        """直接跑一份脚本（未保存的场景/CI 传入）：body {script, spec?}。"""
        from dataclasses import asdict

        script = body.get("script") if isinstance(body, dict) else None
        raw_spec = body.get("spec") if isinstance(body, dict) else None
        if not isinstance(script, dict):
            raise HTTPException(status_code=422, detail="body 须含 script 对象")
        if raw_spec is not None and not isinstance(raw_spec, dict):
            raise HTTPException(status_code=422, detail="spec 必须是流程定义 JSON 对象")
        return asdict(_run(engine, code, script, raw_spec))


def _run(engine: Any, code: str, script: dict[str, Any], spec: dict[str, Any] | None) -> Any:
    """跑场景（草稿校验门槛与 simulate 同口径）。"""
    from flowork.api import run_scenario

    if spec is None:
        spec = _saved_draft_or_none(engine, code)
    if spec is not None:
        definition = parse_spec(engine, spec)
        errors = [i for i in engine.repository.validate(definition) if i.level == IssueLevel.ERROR]
        if errors:
            raise HTTPException(
                status_code=422,
                detail={"message": "校验未通过，禁止场景运行", "issues": issues_payload(errors)},
            )
        return run_scenario(engine, code, script, spec=spec)
    return run_scenario(engine, code, script)


def _saved_draft_or_none(engine: Any, code: str) -> dict[str, Any] | None:
    """取已保存草稿；无草稿返回 None（按已发布版跑）。"""
    from flowork.contrib.web.audit_routes import _saved_draft

    try:
        return _saved_draft(engine, code)
    except HTTPException:
        return None


def _summary_text(report: Any) -> str:
    failed = report.failed
    if not failed:
        return f"✓ 全绿（{len(report.assertions)} 条断言）"
    first = failed[0]
    return (
        f"✕ {len(failed)}/{len(report.assertions)} 条失败："
        f"{first['type']} 期望 {first['expected']} 实际 {first['actual']}"
    )
