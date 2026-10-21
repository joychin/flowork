"""场景回归 web 面：场景脚本随定义文件同住定义目录（`<code>.scenario.json`）。

场景是**可复跑的审阅资产**，必须能进 git、能被 CI 复用——所以它和流程定义一样
以文件为事实源，而不是存进某份数据库。设计器与 CLI `flowork scenario run`
读同一份 JSON（steps/controls/assertions），没有第二份副本。

未配置定义目录时（纯内存会话）场景面不注册：那时候没有可写的文件位置，
给一个存进进程内、关掉就消失的「保存」按钮只会骗人。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from flowork.api import FloworkError, IssueLevel, parse_scenario
from flowork.contrib.web._helpers import issues_payload, parse_spec
from flowork.contrib.web.json_files import read_json, resolve_json_path, root_of, write_json_atomic

__all__ = ["register_scenario_routes", "scenario_file_name"]

# 与定义同目录、同 code、固定后缀：x.json ↔ x.scenario.json
SCENARIO_SUFFIX = ".scenario.json"


def scenario_file_name(code: str) -> str:
    return f"{code}{SCENARIO_SUFFIX}"


def register_scenario_routes(
    app: FastAPI, engine: Any, definitions_dir: str | Path | None = None
) -> None:
    """把场景端点挂到应用。``definitions_dir`` 为空＝无落盘位置，写面不注册。"""
    root = root_of(definitions_dir) if definitions_dir else None

    @app.post("/api/definitions/{code}/scenarios/run")
    def run_inline_scenario(code: str, body: dict[str, Any]) -> dict[str, Any]:
        """直接跑一份脚本（当前编辑中的场景）：body {script, spec?}。零副作用，不需要文件。"""
        script = body.get("script") if isinstance(body, dict) else None
        raw_spec = body.get("spec") if isinstance(body, dict) else None
        if not isinstance(script, dict):
            raise HTTPException(status_code=422, detail="body 须含 script 对象")
        if raw_spec is not None and not isinstance(raw_spec, dict):
            raise HTTPException(status_code=422, detail="spec 必须是流程定义 JSON 对象")
        report: dict[str, Any] = _run(engine, code, script, raw_spec)
        return report

    if root is None:
        return  # 无定义目录＝没有场景文件可读写，写面与读面都不存在

    @app.get("/api/definitions/{code}/scenario")
    def get_scenario(code: str) -> dict[str, Any]:
        """读该定义的场景文件；没有则 404（前端据此显示「新建」）。"""
        path = resolve_json_path(root, scenario_file_name(code))
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{code} 还没有场景文件")
        script = read_json(path)
        try:
            parse_scenario(script)
        except FloworkError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "file": path.name,
            "name": str(script.get("name") or ""),
            "script": script,
        }

    @app.put("/api/definitions/{code}/scenario")
    def save_scenario(code: str, body: dict[str, Any]) -> dict[str, Any]:
        """写场景文件（校验形状后原子落盘，与定义同目录）。"""
        script = body.get("script") if isinstance(body, dict) else None
        if not isinstance(script, dict):
            raise HTTPException(status_code=422, detail="body 须含 script 对象")
        try:
            parse_scenario(script)
        except FloworkError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        path = resolve_json_path(root, scenario_file_name(code))
        write_json_atomic(path, script)
        return {"file": path.name, "name": str(script.get("name") or "")}

    @app.delete("/api/definitions/{code}/scenario")
    def delete_scenario(code: str) -> dict[str, Any]:
        path = resolve_json_path(root, scenario_file_name(code))
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{code} 没有场景文件")
        path.unlink()
        return {"deleted": path.name}


def _run(engine: Any, code: str, script: dict[str, Any], spec: dict[str, Any] | None) -> Any:
    """跑场景（草稿校验门槛与 simulate 同口径）。"""
    from dataclasses import asdict

    from flowork.api import run_scenario

    if spec is not None:
        definition = parse_spec(engine, spec)
        errors = [i for i in engine.repository.validate(definition) if i.level == IssueLevel.ERROR]
        if errors:
            raise HTTPException(
                status_code=422,
                detail={"message": "校验未通过，禁止场景运行", "issues": issues_payload(errors)},
            )
        return asdict(run_scenario(engine, code, script, spec=spec))
    return asdict(run_scenario(engine, code, script))


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
