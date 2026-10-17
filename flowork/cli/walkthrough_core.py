"""演练台核心机件：步间差分观察器 + 剧本装载 + 任务定位与动作分派。

「看得到每一步」的实现思路：所有留痕面（事件 outbox / 路由审计 / 变量血缘 /
待办活表）都是追加或可 diff 的——每次动作前后各看一眼，新增部分就是这张
「步单」的全部素材。观察器只做 diff，不解释语义；解释归 view 渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from flowork.api import KNOWN_TASK_ACTIONS, FloworkError, ProcessEngine

# 动作词表单源：与模拟驱动器的决策清单同一份（api 门面导出）
KNOWN_ACTIONS = KNOWN_TASK_ACTIONS

# 出结果类动作：被办任务自身的离场由动作行交代，不重复报「待办-」；
# 转办/认领类的离场是语义信息（换人接棒），照常报。
OUTCOME_ACTIONS = frozenset({"complete", "reject", "complete_assist", "complete_consult"})


class ScriptError(Exception):
    """剧本或步骤无法执行（带上下文的用户可读消息）。"""


@dataclass
class StepNew:
    """一次动作之后，各留痕面的新增部分（步单素材）。"""

    added: list[Any] = field(default_factory=list)  # TaskInfo
    removed: list[Any] = field(default_factory=list)  # TaskInfo（含被办任务，渲染时过滤）
    routes: list[Any] = field(default_factory=list)  # RouteDecisionInfo
    changes: list[Any] = field(default_factory=list)  # VariableChangeInfo（时间正序）
    events: list[Any] = field(default_factory=list)  # EventInfo（实例序）
    pending: list[Any] = field(default_factory=list)  # 当前待办全量


class RunObserver:
    """按「已见集合」差分四个留痕面；每次 drain 吸收当前全量为新基线。"""

    def __init__(self, engine: ProcessEngine, instance_id: str) -> None:
        self._engine = engine
        self._iid = instance_id
        self._event_ids: set[str] = set()
        self._route_ids: set[str] = set()
        self._last_pending: list[Any] = []
        self._var_seen = 0

    def drain(self) -> StepNew:
        pending = self._engine.task.list_tasks(instance_id=self._iid)
        events = self._engine.history.events(self._iid, limit=2000)
        routes = self._engine.history.route_decisions(self._iid)
        # variable_history 倒序返回且无行 id：翻成正序后按前缀计数差分（追加-only）
        changes = list(reversed(self._engine.runtime.variable_history(self._iid, limit=2000)))
        current_ids = {t.id for t in pending}
        facts = StepNew(
            added=[t for t in pending if t.id not in {p.id for p in self._last_pending}],
            removed=[t for t in self._last_pending if t.id not in current_ids],
            routes=[r for r in routes if r.id not in self._route_ids],
            changes=changes[self._var_seen :],
            events=[e for e in events if e.id not in self._event_ids],
            pending=pending,
        )
        self._last_pending = pending
        self._event_ids = {e.id for e in events}
        self._route_ids = {r.id for r in routes}
        self._var_seen = len(changes)
        return facts


def load_script(path: Any) -> dict[str, Any]:
    """装载并静态校验剧本 JSON（runs 非空、步骤字段齐全、场景级字段类型）。"""
    import json

    try:
        doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScriptError(f"剧本无法读取：{path}（{exc}）") from exc
    runs = doc.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ScriptError("剧本缺少非空 runs 数组")
    for index, run in enumerate(runs, start=1):
        if not isinstance(run, dict) or not isinstance(run.get("steps"), list):
            raise ScriptError(f"剧本第 {index} 个场景缺少 steps 数组")
        for field_name in ("name", "start_user", "business_key"):
            if field_name in run and not isinstance(run[field_name], str):
                raise ScriptError(f"场景 {index} 的 {field_name} 必须是字符串")
        if "variables" in run and not isinstance(run["variables"], dict):
            raise ScriptError(f"场景 {index} 的 variables 必须是对象")
        for step in run["steps"]:
            _validate_step(step, index)
    return doc


def _validate_step(step: Any, run_index: int) -> None:
    if not isinstance(step, dict):
        raise ScriptError(f"场景 {run_index} 存在非对象步骤")
    node, action = step.get("node"), step.get("action")
    if not node or not action:
        raise ScriptError(f"场景 {run_index} 有步骤缺 node/action：{step}")
    if action not in KNOWN_ACTIONS:
        raise ScriptError(
            f"场景 {run_index} 步骤动作未知：{action}（可选 {sorted(KNOWN_ACTIONS)}）"
        )
    if action == "transfer" and not step.get("to"):
        raise ScriptError(f"场景 {run_index}：transfer 步骤必须给 to")
    if action in ("assist", "consult", "add_countersigner") and not _users_of(step):
        raise ScriptError(f"场景 {run_index}：{action} 步骤必须给 users（或 to，逗号分隔）")


def _users_of(step: dict[str, Any]) -> list[str]:
    users = step.get("users")
    if isinstance(users, list) and users:
        return [str(u) for u in users]
    if isinstance(users, str) and users.strip():  # 逗号串形态容忍
        return [part.strip() for part in users.split(",") if part.strip()]
    to = step.get("to")
    if isinstance(to, str) and to.strip():
        return [part.strip() for part in to.split(",") if part.strip()]
    return []


def find_task(engine: ProcessEngine, instance_id: str, step: dict[str, Any]) -> Any:
    """按 node（必要时加 by 对人）定位就绪待办（共享件内核化，CLI 包 ScriptError）。"""
    from flowork.api import find_pending_task

    try:
        return find_pending_task(engine, instance_id, node=step["node"], by=step.get("by"))
    except FloworkError as exc:
        raise ScriptError(str(exc)) from exc


def perform_step(engine: ProcessEngine, task: Any, step: dict[str, Any]) -> None:
    """把一个剧本/交互步骤翻译成 TaskService 调用（动作表全集，共享件内核化）。"""
    from flowork.api import dispatch_task_action

    action = step["action"]
    actor = step.get("by") or task.assignee
    try:
        dispatch_task_action(engine, task.id, action, actor=actor, step=step)
    except FloworkError as exc:
        raise ScriptError(f"{action} 被引擎拒绝：{exc}") from exc


def step_detail(step: dict[str, Any]) -> str:
    """步单「动作」行的人读摘要（comment/variables/to，压缩并限长防截断）。"""
    parts: list[str] = []
    if step.get("comment"):
        parts.append(f"comment={step['comment']}")
    if step.get("variables"):
        pairs = "，".join(f"{key}={value}" for key, value in step["variables"].items())
        parts.append(f"variables={{{pairs}}}")
    if step.get("to"):
        parts.append(f"to={step['to']}")
    detail = "；".join(parts)
    return detail if len(detail) <= 72 else detail[:69] + "…"
