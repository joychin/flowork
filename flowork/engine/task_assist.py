"""协办面（TaskService 组合件，入口 engine.task.assist* 不变）。

从 task_actions.py 拆出（单文件 ≤300 行红线；code-review 2026-10 P1-16
护栏批达 312 行）。协办不占票不卡流程（spec §5.1）：主任务终态同事务收口。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.ops import CommandContext
from flowork.errors import TaskNotAvailableError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import TaskRow
from flowork.tasks.models import TaskInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["TaskAssistMixin"]


class TaskAssistMixin:
    """协办：派生 assist 型任务（独立工作项），主任务收口时同事务取消。"""

    _engine: ProcessEngine  # 由 TaskService.__init__ 提供

    def _instance(self, ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
        """实例加载由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _ready_task(
        self,
        ctx: CommandContext,
        task_id: str,
        expected_type: str | None = None,
        *,
        action: str | None = None,
    ) -> TaskRow:
        """待办加载与守卫（含动作表判定）由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _clone_task(
        self,
        ctx: CommandContext,
        task: TaskRow,
        *,
        assignee: str,
        operator: str,
        task_type: str | None = None,
        root: str | None = None,
    ) -> TaskRow:
        """任务克隆由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _task_info(self, task: TaskRow) -> TaskInfo:
        """任务视图由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _emit_task_event(
        self, ctx: CommandContext, event: DomainEvent, task: TaskRow, actor: str
    ) -> None:
        """事件发射由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def assist(
        self, task_id: str, users: list[str], actor: str, comment: str = ""
    ) -> list[TaskInfo]:
        """协办：为主任务生成 assist 类型任务（完成不影响流程推进）。"""
        result: list[TaskInfo] = self._engine.execute(
            lambda ctx: self._assist(ctx, task_id, users, actor, comment)
        )
        return result

    def _assert_participant_room(
        self, ctx: CommandContext, task: TaskRow, users: list[str], *, task_type: str
    ) -> None:
        """规模护栏（code-review 2026-10 P1-16）：assist/consult 与加签同守——
        同根在办同型任务＋本次请求超 max_participants 即整笔拒绝（None＝不设防）。"""
        limit = self._engine.config.max_participants
        if limit is None:
            return
        from sqlalchemy import select

        root = task.root_task_id or task.id
        open_same = len(
            ctx.session.scalars(
                select(TaskRow.id).where(
                    TaskRow.instance_id == task.instance_id,
                    TaskRow.root_task_id == root,
                    TaskRow.task_type == task_type,
                    TaskRow.state == "ready",
                )
            ).all()
        )
        if len(users) + open_same > limit:
            raise TaskNotAvailableError(
                f"{task_type} 请求 {len(users)} 人＋同根在办 {open_same} 人"
                f"超过上限 {limit}（EngineConfig.max_participants）——拒绝而非截断"
            )

    def _assist(
        self, ctx: CommandContext, task_id: str, users: list[str], actor: str, comment: str
    ) -> list[TaskInfo]:
        task = self._ready_task(ctx, task_id, action="assist")
        self._assert_participant_room(ctx, task, users, task_type="assist")
        ctx.touch(self._instance(ctx, task.instance_id))
        kernel = self._engine.kernel_for(ctx)
        root = task.root_task_id or task.id
        created = [
            self._clone_task(
                ctx, task, assignee=user, operator=actor, task_type="assist", root=root
            )
            for user in users
        ]
        kernel.record_event(
            DomainEvent.TASK_ASSIST_CREATED,
            {
                "task_id": task_id,
                "activity_code": task.activity_code,
                "users": users,
                "actor": actor,
                "comment": comment,
            },
            task.instance_id,
        )
        return [self._task_info(t) for t in created]

    def _cancel_assist_tasks(self, ctx: CommandContext, task: TaskRow, actor: str) -> None:
        """主任务终态（complete/reject）同事务收口其令牌下的协办待办。

        协办不占票也不卡流程（spec §5.1）：主任务已出结果，协办意见失去时效，
        挂着只会变脏待办——收口落历史 outcome=assist_cancel（归因主任务办理人，
        2026-10-13 事件 review 复核批：修前一律记 system，事件缺定位缺 actor）。
        转办不触发（等待未终结，协办随新办理人继续）。
        """
        kernel = self._engine.kernel_for(ctx)
        for assist in kernel.tasks(ctx.session, task.instance_id, token_id=task.token_id):
            if assist.task_type != "assist":
                continue
            kernel.archive_task(
                ctx.session,
                assist,
                outcome="assist_cancel",
                comment="主任务已办理，协办自动收口",
                actor=actor,
            )
            kernel.record_event(
                DomainEvent.TASK_ASSIST_CANCELLED,
                {
                    "task_id": assist.id,
                    "activity_code": assist.activity_code,
                    "root_task_id": task.id,
                    "assignee": assist.assignee,
                    "actor": actor,
                },
                assist.instance_id,
            )

    def complete_assist(self, task_id: str, actor: str, comment: str = "") -> None:
        """完成协办任务：仅归档，不推进流程。"""

        def _command(ctx: CommandContext) -> None:
            task = self._ready_task(ctx, task_id, expected_type="assist", action="complete_assist")
            ctx.touch(self._instance(ctx, task.instance_id))
            kernel = self._engine.kernel_for(ctx)
            kernel.archive_task(ctx.session, task, outcome="assist", comment=comment, actor=actor)
            # 对称补齐（事件 review A7）：协办有 created/cancelled、完成无事件
            kernel.record_event(
                DomainEvent.TASK_ASSIST_FINISHED,
                {
                    "task_id": task.id,
                    "activity_code": task.activity_code,
                    "root_task_id": task.root_task_id,
                    "assignee": task.assignee,
                    "actor": actor,
                },
                task.instance_id,
            )

        self._engine.execute(_command)

    # —— 共享助手（task_rework/task_cc/task_admin 及 task_service 交叉使用）——
