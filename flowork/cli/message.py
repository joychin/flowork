"""message 子命令组（配套面批：等待反查，运维面；publish/correlate 随 REST 插件批）。"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from flowork.api import EngineConfig, FloworkError, ProcessEngine

message_app = typer.Typer(help="消息面（等待反查）", no_args_is_help=True)
console = Console()

DbOption = typer.Option("flowork.db", help="SQLite 文件路径或数据库 URL")


@message_app.command("waiting")
def waiting(
    db: str = DbOption,
    name: str = typer.Option(None, help="按消息名过滤"),
    correlation_key: str = typer.Option(None, help="按关联键过滤"),
) -> None:
    """反查等待中的实例（谁在等什么、等了多久）。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    try:
        rows = engine.messages.waiting(name=name, correlation_key=correlation_key)
        if not rows:
            console.print("[dim]当前没有等待中的订阅[/dim]")
            return
        table = Table(title=f"等待中的订阅（{len(rows)}）")
        for column in ("实例", "节点", "消息名", "关联键", "流程", "业务键", "开始等待"):
            table.add_column(column)
        for row in rows:
            table.add_row(
                row["instance_id"][:8],
                row["node_id"],
                row["name"],
                row["correlation_key"] or "-",
                row["definition_code"],
                row["business_key"] or "-",
                row["waited_since"] or "-",
            )
        console.print(table)
    except FloworkError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        engine.close()
