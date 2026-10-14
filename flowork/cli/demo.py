"""demo / demo-full 命令（自 app.py 迁出守 300 行红线；命令名不变）。

demo：跑通示例 01 并回放事件；demo_full：最终 DoD 演示（分支→会签→驳回→重提→传阅）。
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from flowork.api import (
    FlowBuilder,
    MultiInstance,
    ProcessEngine,
    ProcessVariable,
    RouteMode,
    TaskInfo,
    create_registry,
    load_definition_file,
)

EXAMPLES = Path(__file__).parent.parent.parent / "examples"
console = Console()


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


def demo_full() -> None:
    """最终 DoD 演示：发起 → 分支 → 会签 → 驳回 → 重提 → 传阅 → 结束（事件可回放）。"""
    engine = ProcessEngine()
    _publish_full_demo_flow(engine)  # 演示模板为运行时构建，不依赖 examples 目录
    instance_id = engine.runtime.start_process(
        "full_demo", start_user="u_zhang", variables={"amount": 20_000}
    )
    console.print(f"[bold]1. 发起[/bold] amount=20000 → 实例 {instance_id[:10]}…")

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
    definition = (
        FlowBuilder("full_demo", "全流程演示")
        .start("start", "发起")
        .user_task("manager", "经理审批", assignee="'u_manager'")
        .branch("route", "金额分流", route_mode=RouteMode.FIRST_MATCH)
        .user_task(
            "director",
            "总监会签",
            assignees=["'u_alice'", "'u_bob'"],
            multi_instance=MultiInstance(
                mode="parallel",
                approve_exit="task.approve_count >= task.item_count",
                disapprove_exit="task.disapprove_count >= 1",
            ),
            reject_target="manager",
        )
        .cc_task("notify", "结果传阅", recipients=["initiator"])
        .end("end", "结束")
        .connect("start", "manager")
        .connect("manager", "route")
        .flow("route", "director", condition="amount > 10000")
        .flow("route", "notify", default=True)
        .connect("director", "notify")
        .connect("notify", "end")
        .build()
        .model_copy(update={"variables": [ProcessVariable(name="amount", type="number")]})
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
