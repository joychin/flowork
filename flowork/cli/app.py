"""flowork CLI（M1 最小集：init / validate / demo）。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from flowork.definitions.definition import load_definition_file
from flowork.definitions.validator import IssueLevel, validate_definition
from flowork.engine.engine import ProcessEngine
from flowork.runtime.registry import create_registry

app = typer.Typer(help="flowork 流程引擎 CLI", no_args_is_help=True)
console = Console()


@app.command()
def validate(template: Path = typer.Argument(..., help="模板 JSON 文件路径")) -> None:
    """校验流程模板（结构 / 可达性 / 网关 / kind 注册）。"""
    registry = create_registry()
    definition = load_definition_file(template, registry)
    issues = validate_definition(definition, registry)
    if not issues:
        console.print(f"[green]✓[/green] {definition.code} v{definition.version} 校验通过")
        return
    table = Table(title=f"{template.name} 校验结果")
    table.add_column("级别")
    table.add_column("规则")
    table.add_column("位置")
    table.add_column("说明")
    for issue in issues:
        color = "red" if issue.level == IssueLevel.ERROR else "yellow"
        where = issue.node or issue.flow or "-"
        table.add_row(f"[{color}]{issue.level.value}[/{color}]", issue.rule, where, issue.message)
    console.print(table)
    raise typer.Exit(code=1)


@app.command()
def demo() -> None:
    """跑通示例 01（发起 → 经理审批 → 财务复核 → 结束）并回放事件。"""
    engine = ProcessEngine()
    template = Path(__file__).parent.parent.parent / "examples" / "01_simple_approval.json"
    registry = create_registry()
    definition = load_definition_file(template, registry)
    engine.repository.save_definition(definition)
    engine.repository.publish(definition.code)

    instance_id = engine.runtime.start_process(
        "simple_approval", start_user="u_zhang", variables={"amount": 500}
    )
    console.print(f"实例已发起：[cyan]{instance_id[:12]}…[/cyan]")
    actor_by_activity = {"manager": "u_manager", "finance": "u_finance"}
    for _ in range(5):
        tasks = engine.task.list_tasks(instance_id=instance_id)
        if not tasks:
            break
        task = tasks[0]
        actor = actor_by_activity.get(task.activity_code, task.assignee)
        engine.task.complete(task.id, actor=actor, comment="同意")
        console.print(f"  ✓ {task.name}（{actor}）")

    state = engine.runtime.get_instance(instance_id).state
    console.print(f"实例状态：[green]{state}[/green]")
    table = Table(title="事件回放")
    table.add_column("#", justify="right")
    table.add_column("事件")
    table.add_column("载荷")
    for index, event in enumerate(engine.history.events(instance_id), start=1):
        table.add_row(str(index), event.event_name, str(event.payload))
    console.print(table)
    engine.close()


if __name__ == "__main__":
    app()
