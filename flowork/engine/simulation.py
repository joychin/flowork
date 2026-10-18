"""模拟试跑报告。

数据源全部现查（facts 同款模式，不预存）：令牌时间线＝wf_token、路由轨迹＝
wf_route_decision（模拟跑的每一步边判定天然在表里）、stub 清单＝
到访过的 service_task 节点（行为层模拟分支恒不执行 handler）。**报告诚实呈现
覆盖度**：stub 列表与卡点（wait 冻结/等待中令牌）都是发现，不是噪音。
批 1a 起模拟建真实任务行（机器人经真实动作面办理）——办理记录天然落在
已办行（acted_by=simulator），notes 承载驱动器停点说明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["SimulationReport", "build_report"]


@dataclass
class SimulationReport:
    """一次整图试跑的结果（模拟实例在库里可查、默认被查询面隔离）。"""

    instance_id: str
    definition_code: str
    final_state: str
    visited: list[str] = field(default_factory=list)  # 令牌时间线（激活序）
    stubbed: list[str] = field(default_factory=list)  # service_task 到访（handler 未执行）
    stalled_at: str | None = None  # 卡点（有未完令牌时的所在节点，如 wait 冻结）
    route_decisions: list[dict[str, Any]] = field(default_factory=list)  # 逐步路由轨迹
    notes: list[str] = field(default_factory=list)  # 驱动器停点说明（进报告不上抛）
    decisions: list[dict[str, Any]] = field(default_factory=list)  # 办理记录（已办行推导）
    pending: list[dict[str, Any]] = field(default_factory=list)  # 停住的待办（待决策点）
    stubs_applied: list[str] = field(default_factory=list)  # 消费过桩值的 service 节点
    advanced_minutes: int = 0  # 虚拟时钟累计推进（兑现明细在 notes 与已办行）


def build_report(engine: ProcessEngine, instance_id: str) -> SimulationReport:
    """聚合模拟实例的报告（tokens/stub/卡点/路由轨迹）。

    全树聚合：子流程实例（call_activity/MI）的令牌与已办同样进报告——
    只查根实例会让子流程节点在时间线里凭空消失（决策/待办面早已全树）。
    """

    def _command(ctx: Any) -> SimulationReport:
        from flowork.engine.simulation_controls import applied_stubs
        from flowork.engine.simulation_runner import tree_instance_ids
        from flowork.persistence.orm import ProcessInstanceRow, TokenRow

        instance = ctx.session.get(ProcessInstanceRow, instance_id)
        assert instance is not None
        kernel = engine.kernel_for(ctx)
        tree = tree_instance_ids(ctx, instance_id)
        definitions = {}
        tokens = []
        for inst_id in tree:  # 子实例的定义各是各的（快照不同）
            row = ctx.session.get(ProcessInstanceRow, inst_id)
            if row is None:
                continue
            definitions[inst_id] = kernel.definition_of(ctx.session, row)
            tokens.extend(
                ctx.session.query(TokenRow)
                .filter(TokenRow.instance_id == inst_id)
                .order_by(TokenRow.token_no)
            )
        visited = [token.activity_code for token in tokens]
        stubbed: list[str] = []
        stalled: str | None = None
        for token in tokens:
            definition = definitions.get(token.instance_id)
            node = definition.node(token.activity_code) if definition is not None else None
            is_stub = node is not None and kernel.behavior_of(node).performs_external_work
            if is_stub and token.activity_code not in stubbed:
                stubbed.append(token.activity_code)
            if token.state == "unfinished" and not is_stub and stalled is None:
                stalled = token.activity_code
        decisions = [
            {"node": row["source_activity"], "selected": row["selected"]}
            for row in _route_decisions(ctx, tree)
        ]
        task_actions = _task_records(ctx, tree)
        active = _pending_records(ctx, tree)
        return SimulationReport(
            instance_id=instance_id,
            definition_code=instance.definition_code,
            final_state=instance.state,
            visited=visited,
            stubbed=stubbed,
            stalled_at=stalled,
            route_decisions=decisions,
            decisions=task_actions,
            pending=active,
            stubs_applied=applied_stubs(engine, instance_id),
        )

    return engine.execute(_command)


def _route_decisions(ctx: Any, tree: list[str]) -> list[dict[str, Any]]:
    """路由轨迹（wf_route_decision 现查；模拟实例与真实实例同表同构，全树）。"""
    from sqlalchemy import select

    from flowork.persistence.orm_v2 import RouteDecisionRow

    rows = ctx.session.scalars(
        select(RouteDecisionRow)
        .where(RouteDecisionRow.instance_id.in_(tree))
        .order_by(RouteDecisionRow.id)
    )
    return [{"source_activity": row.source_activity, "selected": row.selected} for row in rows]


def _task_records(ctx: Any, tree: list[str]) -> list[dict[str, Any]]:
    """办理记录（全树已办行，时间正序）——决策扮演/机器人办理的证据面。"""
    from sqlalchemy import select

    from flowork.persistence.orm import TaskHistoryRow

    rows = ctx.session.scalars(
        select(TaskHistoryRow)
        .where(TaskHistoryRow.instance_id.in_(tree))
        .order_by(TaskHistoryRow.created_at, TaskHistoryRow.id)
    )
    return [
        {
            "node": row.activity_code,
            "assignee": row.assignee,
            "acted_by": row.acted_by,
            "outcome": row.outcome,
            "comment": row.comment,
        }
        for row in rows
    ]


def _pending_records(ctx: Any, tree: list[str]) -> list[dict[str, Any]]:
    """停住的待办（待决策点）：驱动结束后仍在活表的任务。"""
    from sqlalchemy import select

    from flowork.persistence.orm import TaskRow

    rows = ctx.session.scalars(
        select(TaskRow).where(TaskRow.instance_id.in_(tree), TaskRow.state == "ready")
    )
    return [
        {
            "task_id": row.id,
            "node": row.activity_code,
            "assignee": row.assignee,
            "task_type": row.task_type,
        }
        for row in rows
    ]
