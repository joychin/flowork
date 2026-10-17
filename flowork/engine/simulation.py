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


def build_report(engine: ProcessEngine, instance_id: str) -> SimulationReport:
    """聚合模拟实例的报告（tokens/stub/卡点/路由轨迹）。"""

    def _command(ctx: Any) -> SimulationReport:
        from flowork.persistence.orm import ProcessInstanceRow, TokenRow

        instance = ctx.session.get(ProcessInstanceRow, instance_id)
        assert instance is not None
        definition = engine.kernel_for(ctx).definition_of(ctx.session, instance)
        tokens = list(
            ctx.session.query(TokenRow)
            .filter(TokenRow.instance_id == instance_id)
            .order_by(TokenRow.token_no)
        )
        visited = [token.activity_code for token in tokens]
        stubbed = [
            token.activity_code
            for token in tokens
            if (node := definition.node(token.activity_code)) is not None
            and engine.kernel_for(ctx).behavior_of(node).performs_external_work
        ]
        stalled = next(
            (
                token.activity_code
                for token in tokens
                if token.state == "unfinished" and token.activity_code not in stubbed
            ),
            None,
        )
        decisions = [
            {"node": row["source_activity"], "selected": row["selected"]}
            for row in _route_decisions(ctx, instance_id)
        ]
        return SimulationReport(
            instance_id=instance_id,
            definition_code=instance.definition_code,
            final_state=instance.state,
            visited=visited,
            stubbed=stubbed,
            stalled_at=stalled,
            route_decisions=decisions,
        )

    return engine.execute(_command)


def _route_decisions(ctx: Any, instance_id: str) -> list[dict[str, Any]]:
    """路由轨迹（wf_route_decision 现查；模拟实例与真实实例同表同构）。"""
    from sqlalchemy import select

    from flowork.persistence.orm_v2 import RouteDecisionRow

    rows = ctx.session.scalars(
        select(RouteDecisionRow)
        .where(RouteDecisionRow.instance_id == instance_id)
        .order_by(RouteDecisionRow.id)
    )
    return [{"source_activity": row.source_activity, "selected": row.selected} for row in rows]
