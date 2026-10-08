"""路由决策审计（expression-dataface §6/F3）：select_outgoing 的旁路记录。

判断面开放后「当时为什么走这条边」必须可回答：每次路由决策一行
（wf_route_decision），edges 逐条件边记录公式与结果（true/false/no_fact 查无
跳过），selected 记录最终激活的连线，snapshot 钉住关键取值（now/prev_task/
node() 引用值，由 routing 求值后回填）——重放解释与事实一致性验收
（§9 F 组）的依据。

求值失败不落行：错误二分（spec §8）下故障单消息已含公式与错误原文。
仅真实路由记录（behavior/branch/incident 三入口）；anticipate 预判与
dry-run 模拟面为只读预演，不产生审计。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = ["RoutingAudit", "snapshot_of"]


class RoutingAudit:
    """一次 select_outgoing 的旁路收集器（routing 填充、调用方持久化）。"""

    def __init__(self) -> None:
        self.source: str = ""
        self.mode: str = ""
        self.edges: list[dict[str, Any]] = []
        self.selected: list[str] = []
        # node() 取值快照（attach_node 闭包回填）：{节点编码: 事实}
        self.node_calls: dict[str, Any] = {}
        # 关键取值快照（routing 求值后经 snapshot_of 回填）
        self.snapshot: dict[str, Any] = {}

    def record_edge(self, flow: str, formula: str, result: str) -> None:
        self.edges.append({"flow": flow, "formula": formula, "result": result})


def _jsonable(value: Any) -> Any:
    """审计快照的 JSON 化：datetime→ISO、timedelta→秒（其余原样/递归）。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def snapshot_of(variables: dict[str, Any], node_calls: dict[str, Any]) -> dict[str, Any]:
    """从求值上下文钉关键取值：now / prev_task / node() 引用值。"""
    snapshot: dict[str, Any] = {}
    if "now" in variables:
        snapshot["now"] = _jsonable(variables["now"])
    if "prev_task" in variables:
        snapshot["prev_task"] = _jsonable(dict(variables["prev_task"]))
    if node_calls:
        snapshot["node"] = _jsonable(node_calls)
    return snapshot


def persist_route_decision(
    session: Session,
    instance_id: str,
    token_id: str | None,
    audit: RoutingAudit,
) -> None:
    """落一行路由决策（与路由同事务；由 behavior/branch/incident 三入口调用）。"""
    import uuid

    from flowork.persistence.orm_v2 import RouteDecisionRow

    session.add(
        RouteDecisionRow(
            id=uuid.uuid4().hex,
            instance_id=instance_id,
            token_id=token_id,
            source_activity=audit.source,
            mode=audit.mode,
            edges=list(audit.edges),
            selected=list(audit.selected),
            snapshot=dict(audit.snapshot),
        )
    )
