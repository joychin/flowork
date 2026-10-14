"""演练台交互模式：你扮演每个角色，一步一办，即时回看引擎决策。

命令语法：``序号 动作``（序号见待办表，动作即 available_actions 里的词），
参数（办理人/意见/变量/转办对象）逐项提示输入；``s`` 看状态、``t`` 看时间线、
``v`` 看变量、``q`` 收束退出（退出前打印完整轨迹与留痕合计）。
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.prompt import Prompt

from flowork.api import ProcessEngine
from flowork.cli import walkthrough_view as view
from flowork.cli.walkthrough_core import (
    KNOWN_ACTIONS,
    RunObserver,
    ScriptError,
    perform_step,
    step_detail,
)


def run_interactive(engine: ProcessEngine, code: str, console: Console, start_user: str) -> None:
    variables = _ask_json("起始流程变量 JSON（回车跳过）")
    instance_id = engine.runtime.start_process(
        code, start_user=start_user, variables=variables or None
    )
    console.print(f"实例已发起：{instance_id}（发起人 {start_user}）")
    observer = RunObserver(engine, instance_id)
    view.print_step(
        console,
        0,
        "?",
        node_label="发起",
        node_code="__start__",
        action="发起",
        actor=start_user,
        detail=f"variables={variables}" if variables else "",
        facts=observer.drain(),
        acted_id=None,
    )
    number = 0
    while True:
        pending = engine.task.list_tasks(instance_id=instance_id)
        if not pending:
            console.print(
                f"[dim]无就绪待办，实例状态：{engine.runtime.get_instance(instance_id).state}[/dim]"
            )
            break
        view.print_pending(console, pending)
        command = Prompt.ask(
            "命令：`序号 动作` 办理；s=状态 t=时间线 v=变量 q=收束", default="s"
        ).strip()
        if command == "q":
            break
        if command == "s":
            continue
        if command == "t":
            view.print_trace(console, engine, instance_id)
            continue
        if command == "v":
            console.print(engine.runtime.variables_of(instance_id))
            continue
        step = _parse_step(command, pending)
        if step is None:
            continue
        task = pending[step.pop("_index")]
        number += 1
        try:
            perform_step(engine, task, step)
        except ScriptError as exc:
            # 交互模式引擎拒绝＝本次动作未生效，提示后续走；脚本模式才中止
            number -= 1
            console.print(f"[red]✗ 动作未生效[/red] {exc}")
            continue
        view.print_step(
            console,
            number,
            "?",
            node_label=task.name,
            node_code=task.activity_code,
            action=step["action"],
            actor=step.get("by") or task.assignee,
            detail=step_detail(step),
            facts=observer.drain(),
            acted_id=task.id,
        )
    view.print_run_summary(console, engine, instance_id)


def _parse_step(command: str, pending: list[Any]) -> dict[str, Any] | None:
    parts = command.split()
    if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in KNOWN_ACTIONS:
        print(f"看不懂「{command}」：形如 `1 complete`；可用动作见待办表右列")
        return None
    index = int(parts[0])
    if not 1 <= index <= len(pending):
        print(f"序号越界：{index}（共 {len(pending)} 条待办）")
        return None
    task = pending[index - 1]
    action = parts[1]
    if action not in task.available_actions:
        print(f"任务当前可用动作不含 {action}：{task.available_actions}")
        return None
    step: dict[str, Any] = {"_index": index - 1, "node": task.activity_code, "action": action}
    step["by"] = Prompt.ask("办理人", default=task.assignee or "").strip()
    if action == "claim" and not step["by"]:
        print("认领必须给办理人（这是谁在领）")
        return None
    comment = Prompt.ask("意见（回车跳过）", default="").strip()
    if comment:
        step["comment"] = comment
    if action == "transfer":
        step["to"] = Prompt.ask("转给谁（必填）").strip()
    if action in ("assist", "consult", "add_countersigner"):
        step["to"] = Prompt.ask("对象人，逗号分隔（必填）").strip()
    if action == "complete":
        variables = _ask_json("提交变量 JSON（回车跳过）")
        if variables is None:  # 三次都没输对＝放弃，取消本步动作（不许空变量带病落库）
            print("已取消本次动作")
            return None
        if variables:
            step["variables"] = variables
    return step


def _ask_json(prompt: str) -> dict[str, Any] | None:
    """交互收集一个 JSON 对象；None＝三次未成功放弃（调用方决定是否取消动作）。"""
    for _ in range(3):
        raw = Prompt.ask(prompt, default="").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            print("不是合法 JSON，重试（或直接回车跳过）")
            continue
        return parsed if isinstance(parsed, dict) else {}
    return None
