"""flowork CLI：init / validate / deploy / retire / purge / drift / tick / 三子命令组 / 演练台。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from flowork.api import (
    EngineConfig,
    FloworkError,
    IssueLevel,
    ProcessEngine,
    create_registry,
    load_definition_file,
    validate_definition,
)
from flowork.cli.demo import demo, demo_full
from flowork.cli.instance import instance_app
from flowork.cli.message import message_app
from flowork.cli.task import task_app
from flowork.cli.walkthrough import walkthrough

app = typer.Typer(help="flowork 流程引擎 CLI", no_args_is_help=True)
for _name, _sub_app in (("instance", instance_app), ("message", message_app), ("task", task_app)):
    app.add_typer(_sub_app, name=_name)
app.command()(demo)
app.command(name="demo-full")(demo_full)
app.command()(walkthrough)
console = Console()


@app.command()
def init(db: str = typer.Option("flowork.db", help="SQLite 文件路径或数据库 URL")) -> None:
    """初始化数据库（建表）。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    console.print(f"[green]✓[/green] 数据库已初始化：{db}")
    engine.close()


@app.command()
def tick(db: str = typer.Option("flowork.db", help="SQLite 文件路径或数据库 URL")) -> None:
    """执行全部到期超时 Job（外部定时器形态：cron/systemd 周期调用）。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    processed = engine.jobs.tick()
    console.print(f"[green]✓[/green] 本次执行 {processed} 个到期 Job（重试/死信见 wf_job）")
    engine.close()


@app.command()
def retire(
    code: str,
    db: str = typer.Option("flowork.db", help="SQLite 文件路径或数据库 URL"),
    reason: str = "",
) -> None:
    """retire 定义：不接新实例，在跑跑完。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    count = engine.repository.retire(code, actor="cli", reason=reason)
    console.print(f"[green]✓[/green] {code} 已 retire（{count} 个已发布版本）")
    engine.close()


@app.command()
def purge(
    code: str,
    db: str = typer.Option("flowork.db", help="SQLite 文件路径或数据库 URL"),
    force: bool = typer.Option(False, "--force", help="物理删除不可逆，必须显式确认"),
) -> None:
    """purge 定义：物理删除全部版本行（仅 CLI；有实例引用则拒绝）。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    removed = engine.repository.purge(code, actor="cli", force=force)
    console.print(f"[green]✓[/green] {code} 已物理删除 {removed} 个版本行")
    engine.close()


@app.command()
def drift(
    directory: Path = typer.Argument(..., help="git 定义目录（*.json）"),
    db: str = typer.Option("flowork.db", help="SQLite 文件路径或数据库 URL"),
) -> None:
    """git 与库对账：未部署/库多余/库落后 三张清单。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    result = engine.repository.definition_drift(str(directory))
    for name, items in result.items():
        console.print(f"[cyan]{name}[/cyan]: {len(items)}")
        for item in items:
            console.print(f"  - {item}")
    engine.close()


@app.command()
def validate(template: Path = typer.Argument(..., help="模板 JSON 文件路径")) -> None:
    """校验流程模板（结构 / 可达性 / 条件分支 / kind 注册）。"""
    registry = create_registry()
    definition = load_definition_file(template, registry)
    issues = validate_definition(definition, registry)
    if not issues:
        console.print(
            f"[green]✓[/green] {definition.code} v{definition.major}.{definition.revision} 校验通过"
        )
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
def deploy(
    template: Path = typer.Argument(..., help="模板 JSON 文件路径"),
    db: str = typer.Option("flowork.db", help="SQLite 文件路径或数据库 URL"),
    target_major: int | None = typer.Option(None, help="维护分支：在该 major 最新 revision 上续发"),
    force_major_reason: str = typer.Option(
        "", help="覆盖破坏性判定为 major+1（必须带理由，进事件）"
    ),
    allow_minor_reason: str = typer.Option(
        "", help="覆盖破坏性判定为 minor 续发（必须带理由，进事件）"
    ),
) -> None:
    """deploy 定义：版本判定 + 发布（deploy 即 publish；含幂等去重与破坏性清单）。"""
    registry = create_registry()
    engine = ProcessEngine(EngineConfig(db_file=db))
    try:
        definition = load_definition_file(template, registry)
        verdict = engine.repository.deploy(
            definition,
            published_by="cli",
            target_major=target_major,
            force_major_reason=force_major_reason,
            allow_minor_reason=allow_minor_reason,
        )
    except FloworkError as exc:
        console.print(f"[red]✗ deploy 失败[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        engine.close()

    if verdict.identical_to is not None:
        console.print(
            f"[yellow]=[/yellow] {definition.code} 内容与 "
            f"v{verdict.identical_to[0]}.{verdict.identical_to[1]} 完全相同——"
            "幂等去重，不增版本不发事件"
        )
        return
    tag = "[red]破坏性[/red]" if verdict.breaking else "兼容"
    version = f"v{verdict.next_major}.{verdict.next_revision}"
    console.print(f"[green]✓[/green] {definition.code} 发布为 {version}（{tag}）")
    if verdict.triggered_rules:
        table = Table(title="触发的破坏性规则")
        table.add_column("规则")
        table.add_column("说明")
        for rule in verdict.triggered_rules:
            table.add_row(rule.rule, rule.detail)
        console.print(table)


if __name__ == "__main__":
    app()
