"""节点事实（expression-dataface §2.2/§2.3/§3）：判断面「现查派生」的只读数据。

两个判断面词汇的数据源：``prev_task``（上一站＝实例内最近一条已完成任务，
含会签最后一票/加签/改派后的实际办理人——目标场景下与「导致本次流转的任务」
等价）与 ``node("code")``（点名查任意节点的聚合事实：轮次/最近轮办理人与耗时/
最近轮会签票数）。同一份底层数据（wf_task_history + wf_token）日后也服务查询面
（REST/Console），口径天然一致。

查无语义（§3）：
- ``prev_task`` 查无＝不注入（条件位经 references_identifier 预检按不满足跳过、
  取人位贡献空）；
- ``node()`` 查无＝**固定形状**（人物字段 null、计数 0）——``==`` 比较自动不满足、
  取人位自动贡献空；对 null 字段做排序/算术比较需判空守卫（CEL 严格类型语义）。
used_time 取 history.cost_seconds（引擎完成事务内已算，自然时长口径）；
runs＝已结束轮数（token.finished_at 非空计数，含被驳回/跳过的轮）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from flowork.persistence.orm import TaskHistoryRow, TokenRow
from flowork.runtime.lifecycle import TokenState

__all__ = [
    "attach_node",
    "node_facts",
    "node_lookup",
    "prev_task_snapshot",
    "with_prev_task",
]

# 排除已取消（兄弟取消/撤回清场）——与 handled_assignees 同口径。
_LIVE_OUTCOMES = TaskHistoryRow.outcome != "cancelled"


def _snapshot_of(row: TaskHistoryRow | None) -> dict[str, Any]:
    """最近轮人物字段（固定形状；无已完成记录 → 全 null，查无=温和）。"""
    if row is None:
        return {
            "assignee": None,
            "outcome": None,
            "comment": None,
            "finished_at": None,
            "used_time": None,
        }
    return {
        "assignee": row.assignee,
        "outcome": row.outcome,
        "comment": row.comment or "",
        "finished_at": row.created_at,  # 已办行的落库时刻＝完成时刻
        "used_time": timedelta(seconds=row.cost_seconds or 0.0),
    }


def prev_task_snapshot(session: Session, instance_id: str) -> dict[str, Any] | None:
    """上一站快照：实例内最近一条已完成任务；无已完成任务 → None（不注入）。"""
    stmt = (
        select(TaskHistoryRow)
        .where(TaskHistoryRow.instance_id == instance_id, _LIVE_OUTCOMES)
        .order_by(TaskHistoryRow.created_at.desc(), TaskHistoryRow.id.desc())
        .limit(1)
    )
    row = session.scalars(stmt).first()
    return _snapshot_of(row) if row is not None else None


def with_prev_task(
    session: Session | None, instance_id: str, variables: Mapping[str, Any]
) -> dict[str, Any]:
    """取人位共用装配：变量副本 + prev_task（查无不加键，贡献空由预检兜底）。"""
    payload = dict(variables)
    if session is not None:
        snapshot = prev_task_snapshot(session, instance_id)
        if snapshot is not None:
            payload.setdefault("prev_task", snapshot)
    return payload


def instance_facts(instance: Any) -> dict[str, Any]:
    """实例全局事实（expression-dataface §2）：id / started_at / business_key。

    全位点注入的全局词（同 initiator/now）；started_at 保持 datetime
    （naive UTC，与 now 同口径，可直接做时长运算）。
    """
    return {
        "id": instance.id,
        "started_at": instance.started_at,
        "business_key": getattr(instance, "business_key", None),
    }


def node_facts(session: Session, instance_id: str, activity_code: str) -> dict[str, Any]:
    """点名节点聚合：runs/skipped + 最近轮人物字段 + 最近轮票数（token 三计数）。"""
    tokens = list(
        session.scalars(
            select(TokenRow).where(
                TokenRow.instance_id == instance_id,
                TokenRow.activity_code == activity_code,
            )
        )
    )
    latest_task = session.scalars(
        select(TaskHistoryRow)
        .where(
            TaskHistoryRow.instance_id == instance_id,
            TaskHistoryRow.activity_code == activity_code,
            _LIVE_OUTCOMES,
        )
        .order_by(TaskHistoryRow.created_at.desc(), TaskHistoryRow.id.desc())
        .limit(1)
    ).first()
    latest_token = max(tokens, key=lambda t: t.token_no, default=None)
    return {
        **_snapshot_of(latest_task),
        "runs": sum(1 for t in tokens if t.finished_at is not None),
        "skipped": latest_token is not None and latest_token.state == TokenState.CANCELLED.value,
        "item_count": latest_token.item_count if latest_token else 0,
        "approve_count": latest_token.approve_count if latest_token else 0,
        "disapprove_count": latest_token.disapprove_count if latest_token else 0,
    }


def node_lookup(
    session: Session, instance_id: str, node_calls: dict[str, Any] | None = None
) -> Callable[[str], Any]:
    """CEL 函数 ``node("code")`` 的工厂（引擎注入，非宿主词汇）。

    node_calls 非空时回填取值快照（路由审计 §6：重放解释要钉住 node() 读到的值）。
    """

    def _node(activity_code: str) -> Any:
        from celpy.adapter import json_to_cel

        facts = node_facts(session, instance_id, str(activity_code))
        if node_calls is not None:
            node_calls.setdefault(str(activity_code), facts)
        # 原生 dict 不支持 celpy 字段选择，须过 json_to_cel（MapType）
        return json_to_cel(facts)

    return _node


def attach_node(
    functions: Mapping[str, Callable[..., object]] | None,
    session: Session | None,
    instance_id: str,
    node_calls: dict[str, Any] | None = None,
) -> Mapping[str, Callable[..., object]]:
    """把 node() 合并进函数表（副本；conditions/subjects 位点共用）。"""
    merged: dict[str, Callable[..., object]] = dict(functions or {})
    if session is not None:
        merged.setdefault("node", node_lookup(session, instance_id, node_calls))
    return merged
