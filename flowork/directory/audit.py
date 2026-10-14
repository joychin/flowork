"""目录写审计：一张追加表回答「谁在何时改了目录」。

与 wf_task_history 同哲学——追加只写、与业务变更同事务；写方法统一经
``record_audit`` 落行（acted_by 缺省 system，REST/CLI 开放面强制传 actor）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from flowork.directory.orm import DirectoryAuditRow

__all__ = ["record_audit"]


def record_audit(
    session: Session,
    action: str,
    entity: str,
    detail: dict[str, Any] | None = None,
    *,
    acted_by: str = "system",
) -> None:
    """落一行目录审计（同事务；action 形如 org_unit.create / role.assign）。"""
    session.add(
        DirectoryAuditRow(
            action=action,
            entity=entity,
            detail=dict(detail or {}),
            acted_by=acted_by,
        )
    )
