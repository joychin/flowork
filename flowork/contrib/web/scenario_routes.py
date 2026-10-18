"""场景回归 web 面：脚本 CRUD、运行（存/直跑）、两版对比。

场景＝steps+controls+assertions 的可复跑脚本（设计器审阅资产，与 CLI
`flowork scenario run` 同一份格式与同一运行器）。与排练面拆两模块守单文件
红线；运行一律经 flowork.api 门面。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from flowork.api import FloworkError, IssueLevel
from flowork.contrib.web._helpers import issues_payload, parse_spec

__all__ = ["register_scenario_routes"]


def register_scenario_routes(app: Any, engine: Any) -> None:
    """把场景端点挂到应用（engine 为 ProcessEngine）。"""

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

    @app.post("/api/scenarios/{scenario_id}/compare")
    def compare_scenario(scenario_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """两版对比跑：草稿 vs 指定已发布版本（同一脚本，红绿并排）。

        body 可给 ``{"spec": 草稿}``（当前未保存编辑）与 ``{"major","revision"}``
        （对照版本，缺省最新发布版）。任一侧跑失败按错误侧如实返回。
        """
        from dataclasses import asdict

        from flowork.api import scenario_store

        row = scenario_store(engine).get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="场景不存在")
        raw = body or {}
        draft_spec = raw.get("spec") if isinstance(raw, dict) else None
        if draft_spec is not None and not isinstance(draft_spec, dict):
            raise HTTPException(status_code=422, detail="spec 必须是流程定义 JSON 对象")
        code = row["definition_code"]
        if draft_spec is None:
            draft_spec = _saved_draft_or_none(engine, code)
        draft_report = None
        if draft_spec is not None:
            draft_report = _safe_run(engine, code, row["script"], spec=draft_spec)
        versions = engine.repository.published_versions(code) or []
        if not versions:
            return {
                "draft": asdict(draft_report) if draft_report is not None else None,
                "published": None,
                "published_version": None,
            }
        major = raw.get("major") if isinstance(raw, dict) else None
        revision = raw.get("revision") if isinstance(raw, dict) else None
        if major is None and revision is None:
            version = versions[0]
        else:
            version = next(
                (v for v in versions if v["major"] == major and v["revision"] == revision),
                None,
            )
            if version is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"版本不存在：v{major}.{revision}（已发布 {len(versions)} 个版本）",
                )
        published_report = _safe_run(
            engine,
            code,
            row["script"],
            major=int(version["major"]),
            revision=int(version["revision"]),
        )
        return {
            "draft": asdict(draft_report) if draft_report is not None else None,
            "published": asdict(published_report) if published_report is not None else None,
            "published_version": f"v{version['major']}.{version['revision']}",
        }

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


def _safe_run(
    engine: Any,
    code: str,
    script: dict[str, Any],
    *,
    spec: dict[str, Any] | None = None,
    major: int | None = None,
    revision: int | None = None,
) -> Any:
    """跑一侧报告；引擎错误转成同形报告（红绿并排不因一侧失败中断）。"""
    from flowork.api import run_scenario

    try:
        return run_scenario(engine, code, script, spec=spec, major=major, revision=revision)
    except (FloworkError, ValueError, TypeError) as exc:
        return ScenarioFailure.of(str(exc))


@dataclass
class ScenarioFailure:
    """一侧跑不起来（校验/引擎错）：ScenarioReport 形状兼容的失败占位（asdict 可用）。"""

    scenario: str = ""
    instance_id: str = ""
    final_state: str = "failed"
    ok: bool = False
    steps: list[Any] = field(default_factory=list)
    assertions: list[Any] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    advanced_minutes: int = 0

    @classmethod
    def of(cls, message: str) -> ScenarioFailure:
        return cls(
            assertions=[
                {"type": "run", "ok": False, "actual": message, "expected": {}, "message": message}
            ],
            notes=[message],
        )

    @property
    def failed(self) -> list[Any]:
        return self.assertions
