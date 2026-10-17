"""模拟控制面：控制模型（决策/桩/等待/时钟）解析校验 + 桩注册表 + 虚拟时钟机件。

- decisions：按序扮演办理人（全量十词表，与演练剧本同构）；
- stubs：service_task 桩返回值——替身 handler，经同一变量写入口注入；
- waits：等待节点按假设兑现（matched 可带载荷，走 output_mappings 同语义）；
- clock：虚拟时钟步进（advance_minutes / advance_to_next_event）——模拟域
  的定时 Job 置 virtual（真实调度器不可见），推钟按 run_at 用真实策略面兑现。

桩注册表按引擎+实例挂（WeakKey 随引擎回收）：服务节点可能在决策推进链中
才到访，桩必须跨命令可用——行为层经 kernel.engine 查这里
（CommandContext 局部态覆盖不到后续命令）。
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = [
    "SimulationControls",
    "applied_stubs",
    "fire_one_due_job",
    "instance_started_at",
    "parse_controls",
    "register_simulation_stubs",
    "simulation_stub_of",
    "step_virtual_clock",
]

_WAIT_RESULTS = ("matched", "elapsed")


@dataclass
class SimulationControls:
    """一次模拟的控制面（JSON 形态与校验见 parse_controls）。"""

    decisions: list[dict[str, Any]] = field(default_factory=list)
    stubs: dict[str, dict[str, Any]] = field(default_factory=dict)
    waits: dict[str, dict[str, Any]] = field(default_factory=dict)  # node -> {result, payload?}
    clock: list[dict[str, Any]] = field(default_factory=list)  # 步进序列
    decisions_given: bool = False  # decisions 键显式给出（含空表）＝禁用机器人


def parse_controls(raw: dict[str, Any] | None) -> SimulationControls:
    """解析并校验 controls（decisions/stubs/waits/clock 四键，全部可选）。"""
    from flowork.tasks.action_steps import KNOWN_TASK_ACTIONS

    if not raw:
        return SimulationControls()
    if not isinstance(raw, dict):
        raise FloworkError("controls 必须是对象")
    controls = SimulationControls()
    decisions = raw.get("decisions")
    if decisions is not None:
        controls.decisions_given = True
        if not isinstance(decisions, list):
            raise FloworkError("controls.decisions 必须是数组")
        for item in decisions:
            if not isinstance(item, dict) or not item.get("node") or not item.get("action"):
                raise FloworkError(f"决策步骤缺少 node/action：{item!r}")
            if item["action"] not in KNOWN_TASK_ACTIONS:
                raise FloworkError(
                    f"决策动作未知：{item['action']}（可选 {sorted(KNOWN_TASK_ACTIONS)}）"
                )
            controls.decisions.append(dict(item))
    stubs = raw.get("stubs")
    if stubs is not None:
        if not isinstance(stubs, dict) or not all(isinstance(v, dict) for v in stubs.values()):
            raise FloworkError("controls.stubs 必须是 {节点: 变量对象}")
        controls.stubs = {str(k): dict(v) for k, v in stubs.items()}
    waits = raw.get("waits")
    if waits is not None:
        if not isinstance(waits, dict):
            raise FloworkError("controls.waits 必须是 {节点: 假设}")
        for node, value in waits.items():
            if isinstance(value, str):
                value = {"result": value}
            if not isinstance(value, dict) or value.get("result") not in _WAIT_RESULTS:
                raise FloworkError(f"等待假设必须是 matched/elapsed（节点 {node}）：{value!r}")
            controls.waits[str(node)] = {
                "result": value["result"],
                "payload": value.get("payload") or None,
            }
    clock = raw.get("clock")
    if clock is not None:
        if not isinstance(clock, list):
            raise FloworkError("controls.clock 必须是数组")
        for step in clock:
            if not isinstance(step, dict):
                raise FloworkError(f"时钟步骤必须是对象：{step!r}")
            if "advance_minutes" in step:
                minutes = step["advance_minutes"]
                if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
                    raise FloworkError(f"advance_minutes 必须是正整数：{minutes!r}")
                controls.clock.append({"advance_minutes": minutes})
            elif step.get("advance_to_next_event"):
                controls.clock.append({"advance_to_next_event": True})
            else:
                raise FloworkError(
                    "时钟步骤须为 {advance_minutes: N} 或 "
                    f"{{advance_to_next_event: true}}：{step!r}"
                )
    return controls


def instance_started_at(engine: ProcessEngine, instance_id: str) -> datetime:
    """虚拟时钟基线＝实例发起时刻（与任务/Job 的 run_at 同钟）。"""

    def _read(ctx: Any) -> datetime:
        from flowork.persistence.orm import ProcessInstanceRow

        row = ctx.session.get(ProcessInstanceRow, instance_id)
        assert row is not None
        started: datetime = row.started_at
        return started

    return engine.execute(_read)


def step_virtual_clock(
    engine: ProcessEngine,
    instance_id: str,
    step: dict[str, Any],
    virtual_now: datetime,
    advanced_minutes: int,
    notes: list[str],
) -> tuple[datetime, int]:
    """消费一个时钟步：返回（新虚拟时刻，累计推钟分钟）。"""

    def _next_run_at(ctx: Any) -> datetime | None:
        from sqlalchemy import select

        from flowork.engine.simulation_runner import tree_instance_ids
        from flowork.persistence.orm import JobRow

        nxt: datetime | None = ctx.session.scalars(
            select(JobRow.run_at)
            .where(
                JobRow.state == "virtual",
                JobRow.run_at > virtual_now,
                JobRow.instance_id.in_(tree_instance_ids(ctx, instance_id)),
            )
            .order_by(JobRow.run_at)
        ).first()
        return nxt

    if step.get("advance_to_next_event"):
        nxt = engine.execute(_next_run_at)
        if nxt is None:
            notes.append("推进到下一事件：无后续定时事件，时刻未变")
            return virtual_now, advanced_minutes
        delta = int((nxt - virtual_now).total_seconds() // 60)
        notes.append(f"推进到下一事件：+{delta} 分钟 → 虚拟时刻 {nxt.isoformat()}")
        return nxt, advanced_minutes + delta
    minutes = int(step["advance_minutes"])
    virtual_now = virtual_now + timedelta(minutes=minutes)
    notes.append(f"推钟 {minutes} 分钟 → 虚拟时刻 {virtual_now.isoformat()}")
    return virtual_now, advanced_minutes + minutes


def fire_one_due_job(
    engine: ProcessEngine, instance_id: str, virtual_now: datetime, notes: list[str]
) -> bool:
    """兑现一个到期的 virtual Job（run_at 最早优先，策略与真实执行同源）。

    证据进 notes（事件面对模拟静默——推钟兑现的可发现性走这里与已办行）。
    到期判定带 5 秒容差：run_at 与虚拟基线是同命令内两次 config.now()，
    真实墙钟下有毫秒级偏移（推钟粒度是分钟，容差诚实无害）。
    """
    from datetime import timedelta

    from sqlalchemy import select

    def _collect(ctx: Any) -> str | None:
        from flowork.engine.simulation_runner import tree_instance_ids
        from flowork.persistence.orm import JobRow

        job_id: str | None = ctx.session.scalars(
            select(JobRow.id)
            .where(
                JobRow.state == "virtual",
                JobRow.run_at <= virtual_now + timedelta(seconds=5),
                JobRow.instance_id.in_(tree_instance_ids(ctx, instance_id)),
            )
            .order_by(JobRow.run_at, JobRow.id)
        ).first()
        return job_id

    job_id = engine.execute(_collect)
    if job_id is None:
        return False
    engine.jobs.run_virtual(job_id, virtual_now)

    def _read(ctx: Any) -> Any:
        from flowork.persistence.orm import JobRow

        return ctx.session.get(JobRow, job_id)

    job = engine.execute(_read)
    payload = job.payload if job else {}
    line = (
        f"虚拟时钟兑现 {payload.get('kind', 'task_deadline')}"
        f"@{payload.get('activity_code', '-')} strategy={payload.get('strategy', '-')}"
    )
    skipped = (job.result_payload or {}).get("skipped") if job else None
    if skipped:
        line += f"（跳过：{skipped}）"
    notes.append(line)
    return True


# —— 桩注册表（引擎级、按实例）——
_STUBS: weakref.WeakKeyDictionary[ProcessEngine, dict[str, dict[str, dict[str, Any]]]] = (
    weakref.WeakKeyDictionary()
)
_STUBS_APPLIED: weakref.WeakKeyDictionary[ProcessEngine, dict[str, list[str]]] = (
    weakref.WeakKeyDictionary()
)


def register_simulation_stubs(
    engine: ProcessEngine, instance_id: str, stubs: dict[str, dict[str, Any]]
) -> None:
    """注册一次模拟的桩值（须在实例启动前——启动链内服务节点即会到访）。"""
    _STUBS.setdefault(engine, {})[instance_id] = stubs


def simulation_stub_of(
    engine: ProcessEngine | None, instance_id: str, node: str
) -> dict[str, Any] | None:
    """取节点桩值并记「已消费」（报告 stubs_applied 数据源）。"""
    if engine is None:
        return None
    stubs = _STUBS.get(engine, {}).get(instance_id)
    if not stubs or node not in stubs:
        return None
    applied = _STUBS_APPLIED.setdefault(engine, {}).setdefault(instance_id, [])
    if node not in applied:
        applied.append(node)
    return stubs[node]


def applied_stubs(engine: ProcessEngine, instance_id: str) -> list[str]:
    """该实例已消费过桩值的 service 节点（报告数据源）。"""
    return list(_STUBS_APPLIED.get(engine, {}).get(instance_id, []))
