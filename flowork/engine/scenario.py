"""场景回归：把模拟控制面写成可复跑的脚本，断言红绿（CI 友好）。

一份脚本、三个消费者（Camunda「设计器用例＝CI 用例」同款范式）：设计器场景
面板、CLI `flowork scenario run`、测试代码——脚本格式与演练剧本同构：

- ``steps``：线性脚本，三类步混排——
  任务动作 ``{node, by?, action, comment?, variables?, target?, to?, users?}``、
  等待兑现 ``{wait: 节点, result: matched|elapsed, payload?}``、
  推钟 ``{clock: {advance_minutes: N} | {advance_to_next_event: true}}``；
- ``controls``：``{stubs, waits, variables}`` 预置（桩/等待默认假设）；
- ``assertions``：业务效果口径（终态/到访/任务行/变量/选路/待办），
  期望值支持 ``equals``/``in``/``contains``/``not_contains``/``op+value``。

运行＝simulated 实例（草稿可跑、零副作用）＋逐步行进；每步之后兑现到期的
virtual Job。覆盖率（节点/边）是照明指标，随报告返回。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["ScenarioReport", "parse_scenario", "run_scenario"]

_MAX_STEPS = 500


@dataclass
class ScenarioReport:
    """一次场景运行的结果：终态/逐步执行/断言红绿/覆盖率/注记。"""

    scenario: str
    instance_id: str
    final_state: str
    ok: bool = False
    steps: list[dict[str, Any]] = field(default_factory=list)  # 逐步执行事实
    assertions: list[dict[str, Any]] = field(default_factory=list)  # 每条断言的判定
    coverage: dict[str, Any] = field(default_factory=dict)  # 节点/边覆盖（照明）
    notes: list[str] = field(default_factory=list)
    advanced_minutes: int = 0

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [item for item in self.assertions if not item["ok"]]


def parse_scenario(raw: Any) -> dict[str, Any]:
    """校验场景脚本形状（steps/controls/assertions，全部可选）。"""
    if not isinstance(raw, dict):
        raise FloworkError("场景必须是对象")
    steps = raw.get("steps")
    if steps is not None:
        if not isinstance(steps, list):
            raise FloworkError("场景 steps 必须是数组")
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise FloworkError(f"第 {index} 步不是对象：{step!r}")
            if not any(key in step for key in ("node", "wait", "clock")):
                raise FloworkError(f"第 {index} 步缺少 node/wait/clock：{step!r}")
    controls = raw.get("controls")
    if controls is not None and not isinstance(controls, dict):
        raise FloworkError("场景 controls 必须是对象")
    assertions = raw.get("assertions")
    if assertions is not None:
        if not isinstance(assertions, list):
            raise FloworkError("场景 assertions 必须是数组")
        for index, item in enumerate(assertions):
            if not isinstance(item, dict) or not item.get("type"):
                raise FloworkError(f"第 {index} 条断言缺少 type：{item!r}")
    return raw


def run_scenario(
    engine: ProcessEngine,
    code: str,
    scenario: dict[str, Any],
    *,
    spec: dict[str, Any] | None = None,
    major: int | None = None,
    revision: int | None = None,
) -> ScenarioReport:
    """跑一个场景：simulated 实例 + 线性脚本 + 断言（草稿可跑，零副作用）。"""
    scenario = parse_scenario(scenario)
    controls_raw = dict(scenario.get("controls") or {})
    # 起始变量：场景顶层 variables（兼容旧写法 controls.variables）
    start_vars = scenario.get("variables") or controls_raw.pop("variables", None)
    report = ScenarioReport(
        scenario=str(scenario.get("name") or "scenario"),
        instance_id="",
        final_state="",
    )
    instance_id, _parsed = _start(
        engine,
        code,
        start_vars,
        controls_raw,
        spec,
        major,
        revision,
        start_user=str(scenario.get("start_user") or "scenario"),
    )
    report.instance_id = instance_id
    virtual_now = _started_at(engine, instance_id)
    advance = _Advance(engine, instance_id, virtual_now, report.notes)

    try:
        _run_steps(engine, instance_id, scenario.get("steps") or [], advance, report)
    except FloworkError as exc:
        report.notes.append(f"脚本停在失败步：{exc}")
    advance.drain(report)
    report.final_state = _final_state(engine, instance_id)
    report.advanced_minutes = advance.advanced
    from flowork.engine.scenario_checks import compute_coverage, evaluate_assertions

    evaluate_assertions(engine, instance_id, scenario.get("assertions") or [], report)
    report.coverage = compute_coverage(engine, instance_id)
    report.ok = not report.failed and report.final_state not in ("failed",)
    return report


def _start(
    engine: ProcessEngine,
    code: str,
    variables: dict[str, Any] | None,
    controls: dict[str, Any],
    spec: dict[str, Any] | None,
    major: int | None,
    revision: int | None,
    start_user: str = "scenario",
) -> tuple[str, Any]:
    instance_id, parsed = engine.runtime.start_simulation(
        code,
        variables=variables,
        start_user=start_user,
        major=major,
        revision=revision,
        spec=spec,
        controls=controls or None,
    )
    return instance_id, parsed


def _started_at(engine: ProcessEngine, instance_id: str) -> datetime:
    from flowork.engine.simulation_controls import instance_started_at

    return instance_started_at(engine, instance_id)


class _Advance:
    """虚拟时钟推进器：推钟后按 run_at 兑现到期 Job（真实策略面）。"""

    def __init__(
        self, engine: ProcessEngine, instance_id: str, virtual_now: datetime, notes: list[str]
    ) -> None:
        self.engine = engine
        self.instance_id = instance_id
        self.virtual_now = virtual_now
        self.notes = notes
        self.advanced = 0

    def step(self, clock: dict[str, Any]) -> None:
        from flowork.engine.simulation_controls import step_virtual_clock

        self.virtual_now, self.advanced = step_virtual_clock(
            self.engine, self.instance_id, clock, self.virtual_now, self.advanced, self.notes
        )
        self.drain(None)

    def drain(self, report: ScenarioReport | None) -> None:
        from flowork.engine.simulation_controls import fire_one_due_job

        for _ in range(_MAX_STEPS):
            if not fire_one_due_job(self.engine, self.instance_id, self.virtual_now, self.notes):
                return


def _run_steps(
    engine: ProcessEngine,
    instance_id: str,
    steps: list[dict[str, Any]],
    advance: _Advance,
    report: ScenarioReport,
) -> None:
    from flowork.engine.rehearsal import perform_rehearsal_step
    from flowork.tasks.action_steps import find_pending_task

    for index, step in enumerate(steps):
        if "clock" in step:
            advance.step(dict(step["clock"]))
            report.steps.append({"index": index, "kind": "clock", "detail": step["clock"]})
            continue
        if "wait" in step:
            perform_rehearsal_step(
                engine,
                instance_id,
                {
                    "kind": "wait",
                    "node": step["wait"],
                    "result": step.get("result"),
                    "payload": step.get("payload"),
                },
            )
            report.steps.append({"index": index, "kind": "wait", "detail": step["wait"]})
            advance.drain(report)
            continue
        task = find_pending_task(engine, instance_id, node=str(step["node"]), by=step.get("by"))
        perform_rehearsal_step(engine, instance_id, {"task_id": task.id, **step})
        report.steps.append(
            {
                "index": index,
                "kind": "task",
                "node": step["node"],
                "action": step.get("action"),
                "task_id": task.id,
            }
        )
        advance.drain(report)


def _final_state(engine: ProcessEngine, instance_id: str) -> str:
    return str(engine.runtime.get_instance(instance_id).state)
