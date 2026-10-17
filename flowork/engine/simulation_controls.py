"""模拟控制面：控制模型（决策/桩/等待）解析校验 + 桩注册表。

- decisions：按序扮演办理人（全量十词表，与演练剧本同构）；
- stubs：service_task 桩返回值——替身 handler，经同一变量写入口注入；
- waits：等待节点按假设兑现（matched 可带载荷，走 output_mappings 同语义）。

桩注册表按引擎+实例挂（WeakKey 随引擎回收）：服务节点可能在决策推进链中
才到访，桩必须跨命令可用——行为层经 kernel.engine 查这里
（CommandContext 局部态覆盖不到后续命令）。
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = [
    "SimulationControls",
    "applied_stubs",
    "parse_controls",
    "register_simulation_stubs",
    "simulation_stub_of",
]

_WAIT_RESULTS = ("matched", "elapsed")


@dataclass
class SimulationControls:
    """一次模拟的控制面（JSON 形态与校验见 parse_controls）。"""

    decisions: list[dict[str, Any]] = field(default_factory=list)
    stubs: dict[str, dict[str, Any]] = field(default_factory=dict)
    waits: dict[str, dict[str, Any]] = field(default_factory=dict)  # node -> {result, payload?}


def parse_controls(raw: dict[str, Any] | None) -> SimulationControls:
    """解析并校验 controls（decisions/stubs/waits 三键，全部可选）。"""
    from flowork.tasks.action_steps import KNOWN_TASK_ACTIONS

    if not raw:
        return SimulationControls()
    if not isinstance(raw, dict):
        raise FloworkError("controls 必须是对象")
    controls = SimulationControls()
    decisions = raw.get("decisions")
    if decisions is not None:
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
    return controls


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
