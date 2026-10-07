"""flowork CLI：init / validate / demo / demo-full（最终 DoD 演示）。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.builtin import RouteMode
from flowork.definitions.definition import load_definition_file
from flowork.definitions.flow import FlowCondition
from flowork.definitions.node import MultiInstance
from flowork.definitions.validator import IssueLevel, validate_definition
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.runtime.registry import create_registry

app = typer.Typer(help="flowork 流程引擎 CLI", no_args_is_help=True)
console = Console()

EXAMPLES = Path(__file__).parent.parent.parent / "examples"


@app.command()
def init(db: Path = typer.Option(Path("flowork.db"), help="SQLite 数据库文件")) -> None:
    """初始化数据库（建表）。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    console.print(f"[green]✓[/green] 数据库已初始化：{db}")
    engine.close()


@app.command()
def tick(db: Path = typer.Option(Path("flowork.db"), help="SQLite 数据库文件")) -> None:
    """执行全部到期超时 Job（外部定时器形态：cron/systemd 周期调用）。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    processed = engine.jobs.tick()
    console.print(f"[green]✓[/green] 本次执行 {processed} 个到期 Job（重试/死信见 wf_job）")
    engine.close()


@app.command()
def retire(code: str, db: Path = typer.Option(Path("flowork.db")), reason: str = "") -> None:
    """retire 定义：不接新实例，在跑跑完（spec §6.2）。"""
    engine = ProcessEngine(EngineConfig(db_file=db))
    count = engine.repository.retire(code, actor="cli", reason=reason)
    console.print(f"[green]✓[/green] {code} 已 retire（{count} 个已发布版本）")
    engine.close()


@app.command()
def purge(
    code: str,
    db: Path = typer.Option(Path("flowork.db")),
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
    db: Path = typer.Option(Path("flowork.db")),
) -> None:
    """git 与库对账：未部署/库多余/库落后 三张清单（spec §6.5）。"""
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
def demo() -> None:
    """跑通示例 01（发起 → 经理审批 → 财务复核 → 结束）并回放事件。"""
    template = EXAMPLES / "01_simple_approval.json"
    if not template.exists():
        console.print(
            "[red]示例模板未随包分发[/red]：请在源码仓库内运行 demo"
            "（examples/ 目录），或用 validate 校验你自己的模板。"
        )
        raise typer.Exit(code=1)
    engine = ProcessEngine()
    definition = load_definition_file(template, create_registry())
    engine.repository.deploy(definition)

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
    _print_events(engine, instance_id)
    engine.close()


@app.command()
def demo_full() -> None:
    """最终 DoD 演示：发起 → 分支 → 会签 → 驳回 → 重提 → 传阅 → 结束（事件可回放）。"""
    engine = ProcessEngine()
    _publish_full_demo_flow(engine)  # 演示模板为运行时构建，不依赖 examples 目录
    instance_id = engine.runtime.start_process(
        "full_demo", start_user="u_zhang", variables={"amount": 20_000}
    )
    console.print(f"[bold]1. 发起[/bold] amount=20000 → 实例 {instance_id[:10]}…")

    from flowork.tasks.models import TaskInfo

    def take(assignee: str) -> TaskInfo:
        return engine.task.list_tasks(instance_id=instance_id, assignee=assignee)[0]

    manager = take("u_manager")
    engine.task.complete(manager.id, actor="u_manager")
    console.print("[bold]2. 分支[/bold] 金额>10000 → 条件分支进入总监会签")

    alice = take("u_alice")
    engine.task.complete(alice.id, actor="u_alice", comment="同意")
    console.print("[bold]3. 会签[/bold] u_alice 同意（1/2）")

    bob = take("u_bob")
    engine.task.reject(bob.id, actor="u_bob", comment="材料需补充")
    console.print("[bold]4. 驳回[/bold] u_bob 否决 → 回退经理，u_manager 重新待办")

    manager2 = take("u_manager")
    engine.task.complete(manager2.id, actor="u_manager", comment="已补充材料")
    console.print("[bold]5. 重提[/bold] 经理重新提交 → 再入总监会签")

    for assignee in ("u_alice", "u_bob"):
        task = take(assignee)
        engine.task.complete(task.id, actor=assignee, comment="同意")
    console.print("[bold]6. 会签完成[/bold] u_alice + u_bob 全员通过")

    cc = engine.task.list_cc_tasks(recipient="u_zhang")
    console.print(f"[bold]7. 传阅[/bold] 结果传阅 u_zhang（{len(cc)} 条），已读={cc[0].is_read}")

    state = engine.runtime.get_instance(instance_id).state
    console.print(f"[bold]8. 结束[/bold] 实例状态：[green]{state}[/green]")

    trace = engine.history.trace_instance(instance_id)
    table = Table(title=f"流转轨迹（{len(trace)} 条）")
    table.add_column("时间")
    table.add_column("类型")
    table.add_column("内容")
    for entry in trace:
        table.add_row(entry.time[11:19], entry.kind, entry.title)
    console.print(table)

    events = [e.event_name for e in engine.history.events(instance_id)]
    assert events[0] == "process_started" and "process_completed" in events
    assert "task_rejected" in events and "task_completed" in events
    console.print(f"[green]✓ 事件回放完整（{len(events)} 条），DoD 演示通过[/green]")
    engine.close()


def _publish_full_demo_flow(engine: ProcessEngine) -> None:
    high_amount = FlowCondition(groups=[[{"left": "amount", "operator": "gt", "right": 10000}]])
    definition = (
        FlowBuilder("full_demo", "全流程演示")
        .start("start", "发起")
        .user_task("manager", "经理审批", assignee="u_manager")
        .branch("route", "金额分流", route_mode=RouteMode.FIRST_MATCH)
        .user_task(
            "director",
            "总监会签",
            assignees=["u_alice", "u_bob"],
            multi_instance=MultiInstance(
                mode="parallel", approve_exit="all", disapprove_exit=">=1"
            ),
            reject_target="manager",
        )
        .cc_task("notify", "结果传阅", recipients=["originator"])
        .end("end", "结束")
        .connect("start", "manager")
        .connect("manager", "route")
        .flow("route", "director", condition=high_amount)
        .flow("route", "notify", default=True)
        .connect("director", "notify")
        .connect("notify", "end")
        .build()
    )
    engine.repository.deploy(definition)


def _print_events(engine: ProcessEngine, instance_id: str) -> None:
    table = Table(title="事件回放")
    table.add_column("#", justify="right")
    table.add_column("事件")
    table.add_column("载荷")
    for index, event in enumerate(engine.history.events(instance_id), start=1):
        table.add_row(str(index), event.event_name, str(event.payload))
    console.print(table)


if __name__ == "__main__":
    app()
