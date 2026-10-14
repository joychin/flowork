"""task 子命令组（候选制配套：我可认领的待办；运维面）。

候选任务查询维度的 CLI 投影——此前候选关联
不经任何公开面暴露，人（与 AI）找不到可认领入口。

``--org`` 传静态目录 YAML（examples/org.yaml 形态）：候选组（``role:`` / ``org:``）
必须经目录才能判归属，不给目录时组候选恒不命中——会与「确实没有待办」混同。
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from flowork.api import EngineConfig, FloworkError, ProcessEngine, StaticOrgDirectory

task_app = typer.Typer(help="任务面（候选待办查询）", no_args_is_help=True)
console = Console()

DbOption = typer.Option("flowork.db", help="SQLite 文件路径或数据库 URL")


@task_app.command("claimable")
def claimable(
    user: str = typer.Argument(..., help="候选人（user: 前缀与裸 id 互认）"),
    db: str = DbOption,
    instance_id: str = typer.Option(None, help="限定实例"),
    org: Path | None = typer.Option(
        None, help="组织目录 YAML（判候选组归属；例 examples/org.yaml）"
    ),
) -> None:
    """列出该用户可认领的待办（候选关联命中：具体人 ∪ 组引用经目录展开）。"""
    try:
        directory = StaticOrgDirectory.from_yaml(org) if org is not None else None
    except FloworkError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(code=1) from exc
    engine = ProcessEngine(EngineConfig(db_file=db, org=directory))
    try:
        rows = engine.task.list_tasks(candidate=user, instance_id=instance_id)
        if not rows:
            console.print(f"[dim]{user} 当前没有可认领的待办[/dim]")
            return
        table = Table(title=f"{user} 可认领的待办（{len(rows)}）")
        for column in ("任务", "节点", "名称", "实例", "创建时间"):
            table.add_column(column)
        for row in rows:
            table.add_row(
                row.id[:8],
                row.activity_code,
                row.name,
                row.instance_id[:8],
                str(row.created_at or "-"),
            )
        console.print(table)
    except FloworkError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        engine.close()
