"""咨询（consult，v2.3 第三批 A8）：前加签的「挂起—拍板」两轮形态。

语义（spec §5.1，行业修正版——不抄 C7 的 assignee 轮转）：
- 「先收集意见、原办理人最后拍板」＝派生 ``task_type=consult`` 的**意见任务**
  （独立工作项：独立记录/意见/审计；不建 deadline job、不进会签计数）；
- **意见不占票**：agree/reject 意见落历史（type=consult 区分），不改
  approve/disapprove 计数、不触发出口——「不占票的加签」走 consult，
  「占票的加签」走 add_countersigner，两路分流（spec 动作票数块）；
- **主任务软挂起**：同链存在未完 consult → 主任务（approval 类）不可操作
  （``TaskNotAvailableError：咨询中``），守卫挂 ``_claim_task_action`` 咽喉，
  不引入新任务状态——意见全部交回即自动解禁（复活零成本）；
- 最后一条意见交回发 ``task_consult_finished`` 事件（宿主据此提醒原办理人）；
- 咨询期间主任务自身的 deadline 时钟照走（咨询不豁免审批时限，防拖）。
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

__all__ = ["TaskConsultMixin"]


class TaskConsultMixin:
    """咨询面；宿主 TaskService 提供 _engine 与共享助手（_ready_task/_clone_task 等）。"""

    _engine: ProcessEngine

    def _assert_participant_room(
        self, ctx: CommandContext, task: TaskRow, users: list[str], *, task_type: str
    ) -> None:
        """规模护栏由 TaskOpsMixin 提供（MRO 前位，桩被遮蔽）。"""
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
        task_type: str | None = None,
        root: str | None = None,
    ) -> TaskRow:
        """任务克隆由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _task_info(self, task: TaskRow) -> TaskInfo:
        """任务视图由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _instance(self, ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
        """实例加载由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    # —— 咨询 ——

    def consult(
        self, task_id: str, users: list[str], actor: str, comment: str = ""
    ) -> list[TaskInfo]:
        """咨询：为主任务派生意见任务（不占票；咨询期间主任务不可操作）。"""
        result: list[TaskInfo] = self._engine.execute(
            lambda ctx: self._consult(ctx, task_id, users, actor, comment)
        )
        return result

    def _consult(
        self, ctx: CommandContext, task_id: str, users: list[str], actor: str, comment: str
    ) -> list[TaskInfo]:
        task = self._ready_task(ctx, task_id, action="consult")
        self._assert_participant_room(ctx, task, users, task_type="consult")  # P1-16
        ctx.touch(self._instance(ctx, task.instance_id))
        kernel = self._engine.kernel_for(ctx)
        root = task.root_task_id or task.id
        created = [
            self._clone_task(ctx, task, assignee=user, task_type="consult", root=root)
            for user in users
        ]
        kernel.record_event(
            DomainEvent.TASK_CONSULT_CREATED,
            {"task_id": task_id, "users": users, "actor": actor, "comment": comment},
            task.instance_id,
        )
        return [self._task_info(t) for t in created]

    def complete_consult(
        self, task_id: str, actor: str, opinion: str = "agree", comment: str = ""
    ) -> None:
        """交回咨询意见：仅归档（outcome=意见，type=consult 区分），不推进流程。"""

        def _command(ctx: CommandContext) -> None:
            self._complete_consult(ctx, task_id, actor, opinion, comment)

        self._engine.execute(_command)

    def _complete_consult(
        self, ctx: CommandContext, task_id: str, actor: str, opinion: str, comment: str
    ) -> None:
        if opinion not in ("agree", "reject"):
            raise TaskNotAvailableError(f"咨询意见只接受 agree/reject：{opinion!r}")
        task = self._ready_task(ctx, task_id, expected_type="consult", action="complete_consult")
        ctx.touch(self._instance(ctx, task.instance_id))
        kernel = self._engine.kernel_for(ctx)
        kernel.archive_task(ctx.session, task, outcome=opinion, comment=comment, actor=actor)
        root = task.root_task_id or task.id
        if not self._open_consults_of(ctx, task.instance_id, root):
            kernel.record_event(
                DomainEvent.TASK_CONSULT_FINISHED,
                {"root_task_id": root, "last_task_id": task.id},
                task.instance_id,
            )

    def _open_consults_of(self, ctx: CommandContext, instance_id: str, root: str) -> list[TaskRow]:
        """同实例、同链根的未完咨询任务。"""
        from sqlalchemy import select

        return list(
            ctx.session.scalars(
                select(TaskRow).where(
                    TaskRow.instance_id == instance_id,
                    TaskRow.root_task_id == root,
                    TaskRow.task_type == "consult",
                    TaskRow.state == "ready",
                )
            )
        )


def assert_no_open_consults(ctx: CommandContext, task: TaskRow) -> None:
    """咨询软挂起守卫（模块级：供 TaskOpsMixin._claim_task_action 内联调用，避开 MRO 遮蔽）。"""
    from sqlalchemy import select

    if task.task_type == "consult":
        return
    root = task.root_task_id or task.id
    open_consult = (
        ctx.session.scalars(
            select(TaskRow.id).where(
                TaskRow.instance_id == task.instance_id,
                TaskRow.root_task_id == root,
                TaskRow.task_type == "consult",
                TaskRow.state == "ready",
            )
        ).first()
        is not None
    )
    if open_consult:
        raise TaskNotAvailableError(f"咨询中：咨询意见未全部交回（主任务暂不可操作）：{task.id}")
