"""表达式求值上下文构造（spec §4.3/C.4 + expression-dataface §2.1）：
全表达式位共用的唯一装配入口。

变量命名空间（spec §4.3 / expression-dataface §2）：``input.*``（契约入参，
存法由调用方约定）、``vars``（实例变量，现状拍平存）、``initiator``（发起人
Subject 保留标识符）、``task``（任务快照，超时改派/会签出口位，spec C.4
固定形状）、``now``（求值时刻，引擎统一时钟注入）。
函数表拍平（``expression_functions_of``）也住这里——expr 是叶子模块，
engine/participants/tasks 各面引用无环；保留词清单的权威在 ``expr.catalog``。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from flowork.clock import utc_now

__all__ = ["expression_context", "expression_functions_of"]


def expression_context(
    variables: Mapping[str, Any],
    *,
    start_user: str | None = None,
    task: Mapping[str, Any] | None = None,
    now: Any = None,
    prev_task: Mapping[str, Any] | None = None,
    instance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造求值上下文副本：变量 + 保留标识符（initiator/instance/now + 可选 task/prev_task）。

    显式同名变量优先（setdefault）：声明了保留词同名变量时不覆盖。
    ``now`` **引擎位点必须显式传入**（``EngineConfig.now``——时钟统一：
    FakeClock 下条件求值、任务时限与路由审计同源）；缺省墙钟仅供无引擎的
    装配面（dry-run 静态预检、web 公式试算——「试算此刻」语义）。
    上下文在求值前装配一次、组内多条公式共用——因此
    **一次求值钉一个时刻**（expression-dataface 验收 E5）。
    ``prev_task`` 仅条件/取人位注入（上一站快照，查无＝不注入，调用方预检温和跳过）。
    ``instance`` 为实例全局事实（id/started_at/business_key，全位点；快照由
    ``tasks.facts.instance_facts`` 构造，模拟面注入占位形状）。
    task 快照做防御性副本——spec C.4 固定形状（task.id / task.assignee /
    task.outcome），活任务（超时触发时）outcome 为 None。
    """
    payload = dict(variables)
    if start_user:
        payload.setdefault("initiator", start_user)
    if task is not None:
        payload.setdefault("task", dict(task))
    if prev_task is not None:
        payload.setdefault("prev_task", dict(prev_task))
    if instance is not None:
        payload.setdefault("instance", dict(instance))
    payload.setdefault("now", utc_now() if now is None else now)
    return payload


def expression_functions_of(config: Any) -> Mapping[str, Callable[..., object]] | None:
    """从 EngineConfig 拍平 CEL 函数表（端口 → {名: 可调用}）；未配置返回 None。"""
    port = getattr(config, "expression_functions", None)
    if port is None:
        return None
    return {name: registered.fn for name, registered in port.functions().items()}
