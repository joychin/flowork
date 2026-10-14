"""walkthrough 命令：流程演练台入口 + 剧本模式驱动。

与 simulate（自动跑通、人不在环）互补：演练发起**真实实例**（缺省内存库，
跑完即弃），由剧本或交互逐动作推进，每步用留痕面差分打印一张步单——
「这一步引擎做了什么决定、为什么」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from flowork.api import (
    EngineConfig,
    FloworkError,
    IssueLevel,
    ProcessEngine,
    create_registry,
    load_definition_file,
    validate_definition,
)
from flowork.cli import walkthrough_view as view
from flowork.cli.walkthrough_core import (
    OUTCOME_ACTIONS,
    RunObserver,
    ScriptError,
    find_task,
    load_script,
    perform_step,
    step_detail,
)

console = Console()


def walkthrough(
    template: Path = typer.Argument(..., help="流程模板 JSON 文件"),
    script: Path | None = typer.Option(
        None, "--script", help="演练剧本 JSON（多场景顺序跑）；缺省进入交互模式"
    ),
    db: str = typer.Option("", "--db", help="持久化库文件/URL；缺省内存库，跑完即弃"),
    start_user: str = typer.Option("u_zhang", help="发起人（交互模式用；剧本场景可覆盖）"),
) -> None:
    """流程演练台：发起真实实例逐步走查，每步打印路由决策/变量变化/事件。"""
    registry = create_registry()
    try:
        definition = load_definition_file(template, registry)
    except FloworkError as exc:
        console.print(f"[red]✗ 模板无法装载[/red] {exc}")
        raise typer.Exit(code=1) from exc
    errors = [i for i in validate_definition(definition, registry) if i.level == IssueLevel.ERROR]
    for issue in errors:
        console.print(f"[red]✗ 设计期校验未过[/red] {issue.rule}：{issue.message}")
    if errors:
        raise typer.Exit(code=1)

    engine = ProcessEngine(EngineConfig(db_file=db) if db else EngineConfig())
    engine.repository.deploy(definition)
    try:
        if script is not None:
            doc = load_script(script)
            for run in doc["runs"]:
                _run_scripted(engine, definition.code, run)
        else:
            from flowork.cli.walkthrough_interactive import run_interactive

            run_interactive(engine, definition.code, console, start_user)
    except ScriptError as exc:
        console.print(f"[red]✗ 演练中止[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        engine.close()


def _run_scripted(engine: ProcessEngine, code: str, run: dict[str, Any]) -> None:
    start_user = run.get("start_user", "u_zhang")
    variables = run.get("variables") or {}
    instance_id = engine.runtime.start_process(
        code,
        start_user=start_user,
        variables=variables or None,
        business_key=run.get("business_key"),
    )
    view.print_run_header(
        console, run.get("name", "未命名场景"), instance_id, run.get("business_key"), variables
    )
    observer = RunObserver(engine, instance_id)
    steps: list[dict[str, Any]] = run["steps"]
    facts = observer.drain()  # 发起本身即第 0 步：首任务/初始事件/无条件首路由
    head = f"business_key={run['business_key']}" if run.get("business_key") else ""
    if variables:
        head = f"{head}，variables={variables}" if head else f"variables={variables}"
    view.print_step(
        console,
        0,
        max(len(steps), 1),
        node_label="发起",
        node_code="__start__",
        action="发起",
        actor=start_user,
        detail=head,
        facts=facts,
        acted_id=None,
    )
    for number, step in enumerate(steps, start=1):
        task = find_task(engine, instance_id, step)
        perform_step(engine, task, step)
        view.print_step(
            console,
            number,
            len(steps),
            node_label=task.name,
            node_code=task.activity_code,
            action=step["action"],
            actor=step.get("by") or task.assignee,
            detail=step_detail(step),
            facts=observer.drain(),
            acted_id=task.id if step["action"] in OUTCOME_ACTIONS else None,
        )
    view.print_run_summary(console, engine, instance_id)
