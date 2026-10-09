"""留痕表序列断言（路由决策 / 变量血缘 / 事件流）。

精神对齐 Zeebe RecordingExporter：断言「写出来的序列对不对」，
而不是只断言「写没写」。形状均经实测钉死（2026-10-09）：
- 路由行 edges 只含**条件边**（默认边不进 edges，落默认道时 selected=默认边、
  edges=各条件边结果）；无条件边也落一行（edges=[]，mode 恒 all_match）。
- snapshot 稀疏：now/instance 恒在，prev_task/node 仅在公式实际引用时出现。
- 变量血缘查询倒序（最新在前），本帮手统一转正序。
"""

from __future__ import annotations

from typing import Any

from flowork.engine.engine import ProcessEngine


def route_rows(engine: ProcessEngine, instance_id: str) -> list[dict[str, Any]]:
    """路由决策（时间正序）：[{source, mode, edges, selected, snapshot_keys}]。"""
    return [
        {
            "source": row.source_activity,
            "mode": row.mode,
            "edges": [(edge["flow"], edge["result"]) for edge in row.edges],
            "selected": list(row.selected),
            "snapshot_keys": set(row.snapshot),
        }
        for row in engine.history.route_decisions(instance_id)
    ]


def route_of(engine: ProcessEngine, instance_id: str, source: str) -> dict[str, Any]:
    """指定节点的唯一路由行（多行=多轮重走，应改用 route_rows 逐行断言）。"""
    rows = [row for row in route_rows(engine, instance_id) if row["source"] == source]
    assert len(rows) == 1, f"{source} 应恰一行路由，实得 {len(rows)} 行：{rows}"
    return rows[0]


def variable_chain(engine: ProcessEngine, instance_id: str, name: str) -> list[dict[str, Any]]:
    """变量血缘（正序）：[{source, acted_by, old, new}]；old=None 表示新建键。"""
    rows = engine.runtime.variable_history(instance_id, name=name, limit=200)
    return [
        {
            "source": row.source,
            "acted_by": row.acted_by,
            "old": row.old_value,
            "new": row.new_value,
        }
        for row in reversed(rows)
    ]


def event_sequence(engine: ProcessEngine, instance_id: str) -> list[str]:
    """事件流（正序）：[event_name, ...]。"""
    return [row.event_name for row in engine.history.events(instance_id, limit=500)]
