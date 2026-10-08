"""令牌记录面：创建/查询/完结/取消（Kernel 组合件，入口类不变）。

从 kernel.py 拆出（单文件 ≤300 行回归）；Kernel 经 Mixin 组合获得本面。
任务查询 ``tasks`` 由 TaskRecordsMixin 提供，本类只留类型桩；
异常类住 kernel.py，需要时函数内延迟导入避免模块环。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from flowork.runtime.lifecycle import TokenState

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.engine.config import EngineConfig
    from flowork.persistence.orm import ProcessInstanceRow, TaskRow, TokenRow

__all__ = ["TokenRecordsMixin"]


def _new_id() -> str:
    return uuid.uuid4().hex


class TokenRecordsMixin:
    """令牌 CRUD 与取消；宿主 Kernel 提供 config 与任务查询。"""

    config: EngineConfig

    def tasks(
        self,
        session: Session,
        instance_id: str,
        *,
        token_id: str | None = None,
        assignee: str | None = None,
    ) -> list[TaskRow]:
        """任务查询由 Kernel（TaskRecordsMixin）提供。"""
        raise NotImplementedError

    def create_token(
        self,
        session: Session,
        instance: ProcessInstanceRow,
        activity_code: str,
        *,
        source_activity_code: str | None = None,
        reject_flag: bool = False,
        item_count: int = 0,
        manual_participants: dict[str, list[str]] | None = None,
    ) -> TokenRow:
        from flowork.persistence.orm import TokenRow as _TokenRow

        existing = self.tokens(session, instance.id)
        next_no = max((t.token_no for t in existing), default=0) + 1
        token = _TokenRow(
            id=_new_id(),
            instance_id=instance.id,
            token_no=next_no,
            activity_code=activity_code,
            source_activity_code=source_activity_code,
            state=TokenState.UNFINISHED.value,
            reject_flag=reject_flag,
            item_count=item_count,
            manual_participants=manual_participants or {},
        )
        session.add(token)
        session.flush()
        return token

    def tokens(
        self, session: Session, instance_id: str, states: tuple[str, ...] | None = None
    ) -> list[TokenRow]:
        from sqlalchemy import select

        from flowork.persistence.orm import TokenRow as _TokenRow

        stmt = (
            select(_TokenRow)
            .where(_TokenRow.instance_id == instance_id)
            .order_by(_TokenRow.token_no)
        )
        rows = list(session.scalars(stmt))
        if states:
            rows = [row for row in rows if row.state in states]
        return rows

    def touch_token(self, session: Session, token: TokenRow) -> None:
        session.flush()

    def finish_token(
        self, session: Session, token: TokenRow, *, approval_exit: str | None = None
    ) -> None:
        token.state = TokenState.FINISHED.value
        token.finished_at = self.config.now()
        if approval_exit:
            token.approval_exit = approval_exit
        session.flush()

    def cancel_token(self, session: Session, token: TokenRow) -> None:
        """取消令牌并撤销其未办任务（任务落历史 outcome=cancelled）。"""
        from flowork.persistence.orm import TaskHistoryRow

        for task in self.tasks(session, token.instance_id, token_id=token.id):
            session.add(
                TaskHistoryRow(
                    id=_new_id(),
                    task_id=task.id,
                    instance_id=task.instance_id,
                    token_id=task.token_id,
                    activity_code=task.activity_code,
                    name=task.name,
                    assignee=task.assignee,
                    task_type=task.task_type,
                    outcome="cancelled",
                    context_payload=task.context_payload,
                    acted_by="system",
                )
            )
            session.delete(task)
        token.state = TokenState.CANCELLED.value
        token.finished_at = self.config.now()
        session.flush()
