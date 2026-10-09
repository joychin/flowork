"""instance 子命令组（变量 I/O 批：首引 add_typer 分组，后续 pause/resume 等 CLI 同组扩展）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from flowork.api import EngineConfig, FloworkError, ProcessEngine

instance_app = typer.Typer(help="实例操作（变量读写/血缘）", no_args_is_help=True)
console = Console()

DbOption = typer.Option(Path("flowork.db"), help="SQLite 数据库文件")


def _parse_assignments(items: list[str]) -> dict[str, Any]:
    """K=V 赋值解析：V 先按 JSON 解析（数字/布尔/结构化），失败退回字符串。"""
    updates: dict[str, Any] = {}
    for item in items:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise typer.BadParameter(f"赋值须为 K=V 形式：{item}")
        try:
            updates[key] = json.loads(raw)
        except ValueError:
            updates[key] = raw
    return updates


@instance_app.command("vars")
def vars_(instance_id: str, db: Path = DbOption) -> None:
    """读实例变量值全集（JSON 输出，供脚本消费）。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    try:
        values = engine.runtime.variables_of(instance_id)
        console.print_json(json.dumps(values, ensure_ascii=False, default=str))
    except FloworkError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        engine.close()


@instance_app.command("vars-set")
def vars_set(
    instance_id: str,
    assignments: list[str] = typer.Argument(..., help="K=V 赋值（V 按 JSON 解析，失败退回字符串）"),
    db: Path = DbOption,
    actor: str = typer.Option(..., "--as", help="操作者（归因必填，写留痕与事件）"),
    reason: str = typer.Option("", "--reason", help="改值原因（入 variables_updated 事件）"),
    as_agent: bool = typer.Option(False, "--as-agent", help="以 agent 身份归因"),
) -> None:
    """外部受控改值（PATCH 合并；白名单/类型/终态三门槛，越线拒收）。"""
    updates = _parse_assignments(assignments)
    engine = ProcessEngine(EngineConfig(db_file=db))
    try:
        result = engine.runtime.update_variables(
            instance_id, updates, actor=actor, as_agent=as_agent, reason=reason
        )
        console.print(f"[green]✓[/green] 已更新：{', '.join(sorted(updates))}（操作者 {actor}）")
        console.print_json(json.dumps(result, ensure_ascii=False, default=str))
    except FloworkError as exc:
        console.print(f"[red]✗ 拒绝[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        engine.close()


@instance_app.command("var-history")
def var_history(
    instance_id: str,
    name: str = typer.Option("", "--name", help="按键过滤"),
    limit: int = typer.Option(50, "--limit"),
    db: Path = DbOption,
) -> None:
    """变量血缘（改前/改后 + 写方归因，倒序）。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    try:
        records = engine.runtime.variable_history(instance_id, name=name or None, limit=limit)
        table = Table(title=f"变量血缘 {instance_id}")
        for col in ("变量", "改前", "改后", "写方", "操作者", "时间"):
            table.add_column(col)
        for r in records:
            table.add_row(
                r.name,
                json.dumps(r.old_value, ensure_ascii=False, default=str),
                json.dumps(r.new_value, ensure_ascii=False, default=str),
                r.source,
                r.acted_by or "-",
                r.created_at,
            )
        console.print(table)
    finally:
        engine.close()
