"""设计器场景仓储：wf_designer_scenario 的 CRUD + 最近运行结论。

挂在 RepositoryService（engine.repository——设计器资产与草稿同域）；
脚本体是语义 JSON（steps/controls/assertions），不混展示坐标。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["ScenarioStore", "scenario_store"]


class ScenarioStore:
    """场景 CRUD（设计器专属；不参与引擎定义生命周期）。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine

    def list(self, definition_code: str) -> list[dict[str, Any]]:
        """某定义的场景列表（新→旧），带最近运行结论摘要。"""

        def _command(ctx: Any) -> list[dict[str, Any]]:
            from sqlalchemy import select

            from flowork.persistence.orm import DesignerScenarioRow

            rows = ctx.session.scalars(
                select(DesignerScenarioRow)
                .where(DesignerScenarioRow.definition_code == definition_code)
                .order_by(DesignerScenarioRow.updated_at.desc())
            )
            return [_summary(row) for row in rows]

        return self._engine.execute(_command)

    def get(self, scenario_id: str) -> dict[str, Any] | None:
        """取单个场景（含完整脚本）。"""

        def _command(ctx: Any) -> dict[str, Any] | None:
            from flowork.persistence.orm import DesignerScenarioRow

            row = ctx.session.get(DesignerScenarioRow, scenario_id)
            if row is None:
                return None
            return {**_summary(row), "script": dict(row.script or {})}

        return self._engine.execute(_command)

    def save(
        self,
        definition_code: str,
        *,
        name: str,
        script: dict[str, Any],
        description: str = "",
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        """新建或更新（给 scenario_id 即更新）。返回摘要。"""

        def _command(ctx: Any) -> dict[str, Any]:
            from flowork.persistence.orm import DesignerScenarioRow

            now = self._engine.config.now()
            row = ctx.session.get(DesignerScenarioRow, scenario_id) if scenario_id else None
            if row is None:
                row = DesignerScenarioRow(
                    id=uuid.uuid4().hex,
                    definition_code=definition_code,
                    name=name,
                    description=description,
                    script=dict(script),
                    created_at=now,
                    updated_at=now,
                )
                ctx.session.add(row)
            else:
                row.name = name
                row.description = description
                row.script = dict(script)
                row.updated_at = now
            ctx.session.flush()
            return _summary(row)

        return self._engine.execute(_command)

    def delete(self, scenario_id: str) -> bool:
        """删除（不存在返回 False）。"""

        def _command(ctx: Any) -> bool:
            from flowork.persistence.orm import DesignerScenarioRow

            row = ctx.session.get(DesignerScenarioRow, scenario_id)
            if row is None:
                return False
            ctx.session.delete(row)
            return True

        return self._engine.execute(_command)

    def record_run(self, scenario_id: str, *, ok: bool, summary: str) -> None:
        """记最近一次运行结论（红绿/时刻/摘要）——列表预览用。"""

        def _command(ctx: Any) -> None:
            from flowork.persistence.orm import DesignerScenarioRow

            row = ctx.session.get(DesignerScenarioRow, scenario_id)
            if row is None:
                return
            row.last_ok = ok
            row.last_run_at = self._engine.config.now()
            row.last_summary = summary

        self._engine.execute(_command)


def scenario_store(engine: ProcessEngine) -> ScenarioStore:
    """取场景仓储（无状态薄壳，按引擎构造）。"""
    return ScenarioStore(engine)


def _summary(row: Any) -> dict[str, Any]:
    script = row.script or {}
    return {
        "id": row.id,
        "definition_code": row.definition_code,
        "name": row.name,
        "description": row.description,
        "step_count": len(script.get("steps") or []),
        "assertion_count": len(script.get("assertions") or []),
        "last_ok": row.last_ok,
        "last_run_at": row.last_run_at.isoformat(timespec="seconds")
        if row.last_run_at is not None
        else None,
        "last_summary": row.last_summary,
        "updated_at": row.updated_at.isoformat(timespec="seconds"),
    }
