"""传阅面（TaskService 组合件，入口 engine.task.* 不变）。

从 task_actions.py 拆出（单文件 ≤300 行回归）；CcItemInfo 一并迁此。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from flowork.engine.ops import CommandContext
from flowork.errors import TaskNotAvailableError
from flowork.persistence.orm import CcItemRow

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["CcItemInfo", "TaskCcMixin"]


class CcItemInfo(BaseModel):
    """一条传阅件。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    instance_id: str
    activity_code: str
    recipient: str
    title: str
    is_read: bool = False
    read_at: datetime | None = None
    created_at: datetime | None = None


class TaskCcMixin:
    """传阅件查询与已读标记；宿主 TaskService 提供 _engine/_instance。"""

    _engine: ProcessEngine

    def _instance(self, ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
        """实例加载由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def list_cc_tasks(
        self, *, recipient: str | None = None, instance_id: str | None = None
    ) -> list[CcItemInfo]:
        """查询传阅件（可按收件人/实例过滤）。"""
        from sqlalchemy import select

        def _command(ctx: CommandContext) -> list[CcItemInfo]:
            stmt = select(CcItemRow).order_by(CcItemRow.created_at)
            if recipient:
                stmt = stmt.where(CcItemRow.recipient == recipient)
            if instance_id:
                stmt = stmt.where(CcItemRow.instance_id == instance_id)
            return [self._cc_info(row) for row in ctx.session.scalars(stmt)]

        return self._engine.execute(_command)

    def mark_cc_read(self, cc_id: str, user: str) -> None:
        """标记传阅件已读。"""

        def _command(ctx: CommandContext) -> None:
            row = ctx.session.get(CcItemRow, cc_id)
            if row is None or row.recipient != user:
                raise TaskNotAvailableError(f"传阅件不存在或不属于 {user}：{cc_id}")
            ctx.touch(self._instance(ctx, row.instance_id))
            row.is_read = True
            row.read_at = ctx.engine.config.now()

        self._engine.execute(_command)

    def _cc_info(self, row: CcItemRow) -> CcItemInfo:
        return CcItemInfo(
            id=row.id,
            instance_id=row.instance_id,
            activity_code=row.activity_code,
            recipient=row.recipient,
            title=row.title,
            is_read=row.is_read,
            read_at=row.read_at,
            created_at=row.created_at,
        )
