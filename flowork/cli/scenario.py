"""CLI 场景命令：`flowork scenario run`（CI 口：断言红绿决定退出码）。

与设计器场景面板同一份脚本格式（steps/controls/assertions）与同一运行器
（engine/scenario）——「设计器里编用例、CI 里跑同一份文件」（行业对标范式，
Camunda 设计器用例=CI 用例已亲验）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from flowork.api import (
    EngineConfig,
    FloworkError,
    IssueLevel,
    ProcessEngine,
    create_registry,
    run_scenario,
)

scenario_app = typer.Typer(help="场景回归：可复跑脚本 + 断言（CI 友好）", no_args_is_help=True)
console = Console()

__all__ = ["scenario_app"]


@scenario_app.command("run")
def run(
    definition: Path = typer.Argument(..., help="流程定义 JSON 文件"),
    scenario: Path = typer.Argument(..., help="场景脚本 JSON 文件"),
    db: str = typer.Option("flowork.db", help="SQLite 文件路径或数据库 URL"),
    deploy: bool = typer.Option(False, "--deploy", help="先部署该定义（按文件内容发布）再跑场景"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="打印逐步执行明细"),
) -> None:
    """跑场景：全绿退出码 0，任一断言失败 1，脚本/引擎错误 2。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    try:
        if deploy:
            registry = create_registry()
            built = _definition_for(definition, registry)
            engine.repository.deploy(built, published_by="cli")
        spec = _load_json(definition)
        script = _load_json(scenario)
        report = _run(engine, definition, spec, script)
    except FloworkError as exc:
        console.print(f"[red]✗ 场景失败[/red] {exc}")
        raise typer.Exit(code=2) from exc
    finally:
        engine.close()

    _print_report(report, verbose)
    if not report.ok:
        # 停在失败步/断言失败/终态 failed 一律非绿（2026-10-21 内核 review
        # P1-7：修前只看 failed——脚本中途停住而断言在部分状态上恰巧成立时
        # 退出码 0，坏掉的回归脚本在 CI 里是绿的）
        raise typer.Exit(code=1)


def _definition_for(path: Path, registry: Any) -> Any:
    from flowork.api import load_definition_file

    try:
        return load_definition_file(path, registry)
    except OSError as exc:
        raise FloworkError(f"读取定义文件失败 {path}：{exc}") from exc


def _load_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FloworkError(f"读取 {path} 失败：{exc}") from exc
    if not isinstance(loaded, dict):
        raise FloworkError(f"{path} 必须是 JSON 对象")
    return loaded


def _run(
    engine: ProcessEngine, definition: Path, spec: dict[str, Any], script: dict[str, Any]
) -> Any:
    code = str(spec.get("code") or "")
    if not code:
        raise FloworkError(f"{definition} 缺少 code")
    # 与 web 同门槛：设计期 ERROR 拦（否则 CI 会放过设计器拒跑的定义）
    from flowork.api import load_definition

    issues = engine.repository.validate(load_definition(spec, engine.config.node_registry))
    errors = [issue for issue in issues if issue.level == IssueLevel.ERROR]
    if errors:
        detail = "；".join(f"{e.rule} {e.message}" for e in errors[:3])
        raise FloworkError(f"定义校验未通过（{len(errors)} 个错误）：{detail}")
    return run_scenario(engine, code, script, spec=spec)


def _print_report(report: Any, verbose: bool) -> None:
    if verbose:
        table = Table(title="逐步执行")
        table.add_column("#")
        table.add_column("类别")
        table.add_column("明细")
        for step in report.steps:
            detail = step.get("node") or step.get("detail") or ""
            action = step.get("action") or ""
            table.add_row(
                str(step.get("index")), str(step.get("kind")), f"{detail} {action}".strip()
            )
        console.print(table)

    table = Table(title=f"场景断言（{report.scenario}）")
    table.add_column("结果")
    table.add_column("类型")
    table.add_column("期望")
    table.add_column("实际")
    for item in report.assertions:
        mark = "[green]✓[/green]" if item["ok"] else "[red]✗[/red]"
        table.add_row(
            mark,
            item["type"],
            json.dumps(item["expected"], ensure_ascii=False),
            json.dumps(item["actual"], ensure_ascii=False)[:60],
        )
    console.print(table)

    nodes = report.coverage.get("nodes") or {}
    if nodes:
        missing = nodes.get("missing") or []
        console.print(
            f"覆盖照明：节点 {len(nodes.get('covered') or [])}/{nodes.get('total')}"
            + (f"（未覆盖 {', '.join(missing)}）" if missing else "（全覆盖）")
        )
    if report.notes:
        for note in report.notes:
            console.print(f"[yellow]⚠[/yellow] {note}")
    if report.stopped:
        verdict = "[red]✗ 脚本停在失败步（部分状态上的断言不作数）[/red]"
    elif report.failed:
        verdict = f"[red]✗ {len(report.failed)} 条失败[/red]"
    else:
        verdict = "[green]✓ 全绿[/green]"
    console.print(f"{verdict}（终态 {report.final_state}，实例 {report.instance_id[:8]}…）")
