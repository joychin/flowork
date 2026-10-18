"""令牌查询面（HistoryService 组合件，拆分守行数红线）。

令牌是实例的推进实体（token_no 序）：状态 unfinished/completed/cancelled，
审计面按它看「走到哪、还有几票没办」。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from flowork.engine.ops import CommandContext
from flowork.persistence.orm import TokenRow
from flowork.tasks.models import TokenInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["HistoryTokenMixin"]


class HistoryTokenMixin:
    """令牌查询（宿主 HistoryService 提供 _engine）。"""

    _engine: ProcessEngine

    def tokens(self, instance_id: str) -> list[TokenInfo]:
        def _command(ctx: CommandContext) -> Any:
            stmt = (
                select(TokenRow)
                .where(TokenRow.instance_id == instance_id)
                .order_by(TokenRow.token_no)
            )
            return [
                TokenInfo(
                    id=row.id,
                    token_no=row.token_no,
                    activity_code=row.activity_code,
                    source_activity_code=row.source_activity_code,
                    state=row.state,
                    item_count=row.item_count,
                    approve_count=row.approve_count,
                    disapprove_count=row.disapprove_count,
                    reject_flag=row.reject_flag,
                    approval_exit=row.approval_exit,
                    created_at=row.created_at,
                    finished_at=row.finished_at,
                )
                for row in ctx.session.scalars(stmt)
            ]

        result: list[TokenInfo] = self._engine.execute(_command)
        return result
