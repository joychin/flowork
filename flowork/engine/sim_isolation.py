"""模拟实例隔离的共用判定（查询面 join 口径单源）。

模拟域实例（simulated=True）的运行时行是**真实行**（批 1a 任务行化起）——
业务查询面默认把它们排除，审计/驱动器面按 instance_id 定向时不排除。
三个查询面（list_tasks / paged_tasks / task_history）共用本件，防口径漂移。
"""

from __future__ import annotations

from typing import Any

__all__ = ["exclude_simulated"]

_SIMULATED_EXCLUDED = ("list_tasks", "paged_tasks", "task_history")


def exclude_simulated(stmt: Any, instance_column: Any) -> Any:
    """给任务类查询加「排除模拟实例」的 join 条件。

    ``instance_column``＝该表指向实例 id 的列（TaskRow.instance_id 等）。
    """
    from flowork.persistence.orm import ProcessInstanceRow

    return stmt.join(ProcessInstanceRow, ProcessInstanceRow.id == instance_column).where(
        ProcessInstanceRow.simulated.is_(False)
    )
