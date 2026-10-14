"""演练台渲染：把 StepNew 差分事实排成一张人读「步单」。

叙事视角（第 N 步发生了什么）而非审计视角（按数据维度分面板）——每步固定
报「动作 → 待办增减 → 路由决策及理由 → 变量变化 → 事件」，让你逐行核对
引擎的每个决定。动态内容一律 escape，防 CEL 公式里的方括号被当 rich 标记。
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

_ACTION_ZH = {
    "complete": "办理",
    "reject": "驳回",
    "transfer": "转办",
    "assist": "协办",
    "consult": "咨询",
    "claim": "认领",
    "unclaim": "取消认领",
    "add_countersigner": "加签",
    "complete_assist": "协办交回",
    "complete_consult": "意见交回",
}

_EVENT_KEYS = ("actor", "activity_code", "outcome", "assignee", "by", "reason", "comment")


def print_run_header(
    console: Console,
    name: str,
    instance_id: str,
    business_key: str | None,
    variables: dict[str, Any],
) -> None:
    key = f"business_key={business_key}，" if business_key else ""
    console.print(
        f"\n[bold]▶ 场景：{escape(name)}[/bold]（{key}实例 {instance_id[:8]}…，"
        f"起始变量 {variables or '无'}）"
    )


def print_step(
    console: Console,
    number: int,
    total: int | str,
    *,
    node_label: str,
    node_code: str,
    action: str,
    actor: str,
    detail: str,
    facts: Any,
    acted_id: str | None,
) -> None:
    action_zh = "发起" if node_code == "__start__" else _ACTION_ZH.get(action, action)
    console.print(
        f"\n[bold cyan]── 步骤 {number}/{total} · {escape(actor)} · {escape(node_label)}"
        f"({escape(node_code)}) · {escape(action_zh)} ──[/bold cyan]"
    )
    _line(console, "动作", f"{action}{('（' + detail + '）') if detail else ''}", "dim")
    for task in facts.added:
        _line(console, "待办+", f"{task.name}({task.activity_code}) → {task.assignee}", "green")
    for task in facts.removed:
        if task.id != acted_id:  # 被办任务本身不用重复报（动作行已含）
            _line(console, "待办-", f"{task.name}({task.activity_code}) → {task.assignee}", "red")
    for route in facts.routes:
        text = _route_text(route)
        if text:
            _line(console, "路由", text, "yellow")
    for change in facts.changes:
        _line(
            console,
            "变量",
            f"{change.name}: {change.old_value} → {change.new_value}"
            f"（{change.source}·{change.acted_by}）",
            "magenta",
        )
    for event in facts.events:
        _line(console, "事件", _event_text(event), "dim")
    if not facts.events:
        _line(console, "事件", "无", "dim")
    if facts.pending:
        waiting = "、".join(f"{t.activity_code}→{t.assignee}" for t in facts.pending)
        _line(console, "在途", waiting, "bright_black")
    else:
        _line(console, "在途", "无就绪待办", "bright_black")


def _route_text(route: Any) -> str:
    selected = "、".join(route.selected)
    formulas = [e for e in route.edges if e.get("formula")]
    if not formulas:  # 默认边不进 edges：无公式可报时只报选中；终点行（无源无选中）是噪音
        if not selected:
            return ""
        return f"{route.source_activity} → {selected}（无条件/默认边）"
    verdicts = "；".join(f"{e['formula']} = {e['result']}" for e in formulas)
    return f"{route.source_activity}({route.mode})｜{verdicts} → 选中 {selected}"


def _event_text(event: Any) -> str:
    picked: list[str] = []
    for key in _EVENT_KEYS:
        if event.payload.get(key) is not None and len(picked) < 3:
            picked.append(f"{key}={_short(event.payload[key])}")
    for key, value in event.payload.items():  # 没命中偏好键时兜底取前三
        if len(picked) >= 3:
            break
        if value is not None and f"{key}=" not in "；".join(picked):
            picked.append(f"{key}={_short(value)}")
    name = str(event.event_name)
    return name + ("{" + "，".join(picked) + "}" if picked else "")


def _short(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 24 else text[:21] + "…"


def _line(console: Console, label: str, text: str, style: str) -> None:
    console.print(
        f"  [{style}]{label:<3}[/{style}] {escape(text)}", no_wrap=True, overflow="ignore"
    )


def print_pending(console: Console, tasks: list[Any]) -> None:
    if not tasks:
        console.print("[dim]当前无就绪待办[/dim]")
        return
    table = Table(title=f"就绪待办（{len(tasks)}）")
    for column in ("序号", "节点", "名称", "办理人", "可用动作"):
        table.add_column(column)
    for index, task in enumerate(tasks, start=1):
        table.add_row(
            str(index),
            task.activity_code,
            task.name,
            task.assignee or "（候选池）",
            "、".join(task.available_actions) or "-",
        )
    console.print(table)


def print_trace(console: Console, engine: Any, instance_id: str) -> None:
    trace = engine.history.trace_instance(instance_id)
    table = Table(title=f"流转轨迹（{len(trace)} 条）")
    for column in ("时间", "类型", "内容"):
        table.add_column(column)
    for entry in trace:
        table.add_row(entry.time[11:19], entry.kind, entry.title)
    console.print(table)


def print_run_summary(console: Console, engine: Any, instance_id: str) -> None:
    instance = engine.runtime.get_instance(instance_id)
    console.print(f"\n[bold]■ 收束[/bold] 实例 {instance_id[:8]}… 状态 = {instance.state}")
    pending = engine.task.list_tasks(instance_id=instance_id)
    if pending:
        waiting = "、".join(f"{t.activity_code}→{t.assignee}" for t in pending)
        console.print(f"[yellow]⚠ 未走完（剧本止于此）：剩余待办 {waiting}[/yellow]")
    print_trace(console, engine, instance_id)
    events = engine.history.events(instance_id, limit=2000)
    routes = engine.history.route_decisions(instance_id)
    changes = engine.runtime.variable_history(instance_id, limit=2000)
    console.print(
        f"留痕合计：事件 {len(events)} 条 · 路由决策 {len(routes)} 行 · 变量变更 {len(changes)} 条"
    )
