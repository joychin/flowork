"""任务操作集核心（M3）：认领 / 转办 / 协办 + 共享助手。

以 Mixin 形式并入 TaskService（保持四服务 API：engine.task.*）。
传阅/加签撤回/管理员操作拆至 task_cc.py / task_rework.py / task_admin.py（入口不变）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError, TaskNotAvailableError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import (
    IdentityLinkRow,
    TaskRow,
)
from flowork.tasks.models import TaskInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow
    from flowork.persistence.orm import TaskRow as _TaskRowT

__all__ = ["TaskOpsMixin"]


class TaskOpsMixin:
    """任务操作集：方法经 Command 管线执行，全部带 actor 归因。"""

    _engine: ProcessEngine  # 由 TaskService.__init__ 提供

    # —— 认领（候选任务）——

    def claim(self, task_id: str, user: str) -> TaskInfo:
        """候选人认领任务：assignee 置为本人，清除候选关联。"""
        result: TaskInfo = self._engine.execute(lambda ctx: self._claim(ctx, task_id, user))
        return result

    def _claim(self, ctx: CommandContext, task_id: str, user: str) -> TaskInfo:
        from sqlalchemy import select

        task = ctx.session.get(TaskRow, task_id)
        if task is None or task.state != "ready":
            raise TaskNotAvailableError(f"任务不可认领（不存在或已处理）：{task_id}")
        self._assert_operable(ctx, task)
        ctx.touch(self._instance(ctx, task.instance_id))
        candidates = {
            row.user_id
            for row in ctx.session.scalars(
                select(IdentityLinkRow).where(IdentityLinkRow.task_id == task_id)
            )
        }
        if task.assignee == "":
            if user not in candidates:
                raise TaskNotAvailableError(f"用户 {user} 不是任务 {task_id} 的候选人")
            task.assignee = user
            ctx.session.flush()
            self._emit_task_event(ctx, DomainEvent.TASK_CLAIMED, task, user)
            return self._task_info(task)
        if task.assignee != user:  # 已被认领/直派
            raise TaskNotAvailableError(f"任务已被 {task.assignee} 认领")
        return self._task_info(task)

    def unclaim(self, task_id: str, user: str) -> TaskInfo:
        """取消认领：任务回到候选池。"""
        result: TaskInfo = self._engine.execute(lambda ctx: self._unclaim(ctx, task_id, user))
        return result

    def _unclaim(self, ctx: CommandContext, task_id: str, user: str) -> TaskInfo:
        task = ctx.session.get(TaskRow, task_id)
        if task is None or task.state != "ready" or task.assignee != user:
            raise TaskNotAvailableError(f"任务不可取消认领：{task_id}")
        ctx.touch(self._instance(ctx, task.instance_id))
        task.assignee = ""
        ctx.session.flush()
        return self._task_info(task)

    # —— 转办 ——

    def transfer(self, task_id: str, to_user: str, actor: str, comment: str = "") -> TaskInfo:
        """转办：当前任务归档（outcome=transfer），同令牌为 to_user 生成新待办。"""
        result: TaskInfo = self._engine.execute(
            lambda ctx: self._transfer(ctx, task_id, to_user, actor, comment)
        )
        return result

    def _transfer(
        self, ctx: CommandContext, task_id: str, to_user: str, actor: str, comment: str
    ) -> TaskInfo:
        task = self._ready_task(ctx, task_id)
        ctx.touch(self._instance(ctx, task.instance_id))
        kernel = self._engine.kernel_for(ctx)
        self._emit_task_event(ctx, DomainEvent.TASK_TRANSFERRED, task, actor)
        new_task = self._clone_task(ctx, task, assignee=to_user, root=task.root_task_id or task.id)
        kernel.archive_task(ctx.session, task, outcome="transfer", comment=comment, actor=actor)
        return self._task_info(new_task)

    # —— 协办 ——

    def assist(
        self, task_id: str, users: list[str], actor: str, comment: str = ""
    ) -> list[TaskInfo]:
        """协办：为主任务生成 assist 类型任务（完成不影响流程推进）。"""
        result: list[TaskInfo] = self._engine.execute(
            lambda ctx: self._assist(ctx, task_id, users, actor, comment)
        )
        return result

    def _assist(
        self, ctx: CommandContext, task_id: str, users: list[str], actor: str, comment: str
    ) -> list[TaskInfo]:
        task = self._ready_task(ctx, task_id)
        ctx.touch(self._instance(ctx, task.instance_id))
        kernel = self._engine.kernel_for(ctx)
        created = [self._clone_task(ctx, task, assignee=user, task_type="assist") for user in users]
        kernel.record_event(
            "task_assist_created",
            {"task_id": task_id, "users": users, "actor": actor, "comment": comment},
            task.instance_id,
        )
        return [self._task_info(t) for t in created]

    def complete_assist(self, task_id: str, actor: str, comment: str = "") -> None:
        """完成协办任务：仅归档，不推进流程。"""

        def _command(ctx: CommandContext) -> None:
            task = self._ready_task(ctx, task_id, expected_type="assist")
            ctx.touch(self._instance(ctx, task.instance_id))
            kernel = self._engine.kernel_for(ctx)
            kernel.archive_task(ctx.session, task, outcome="assist", comment=comment, actor=actor)

        self._engine.execute(_command)

    # —— 共享助手（task_rework/task_cc/task_admin 及 task_service 交叉使用）——

    def _instance(self, ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
        from flowork.persistence.orm import ProcessInstanceRow as _Row

        instance = ctx.session.get(_Row, instance_id)
        if instance is None:
            raise InstanceNotFoundError(f"实例不存在：{instance_id}")
        return instance

    def _ready_task(
        self, ctx: CommandContext, task_id: str, expected_type: str | None = None
    ) -> _TaskRowT:
        task = ctx.session.get(TaskRow, task_id)
        if task is None or task.state != "ready":
            raise TaskNotAvailableError(f"任务不可操作（不存在或已处理）：{task_id}")
        self._assert_operable(ctx, task)
        if expected_type and task.task_type != expected_type:
            raise TaskNotAvailableError(f"任务类型不符（期望 {expected_type}）：{task_id}")
        return task

    def _assert_operable(self, ctx: CommandContext, task: TaskRow) -> None:
        """任务动作统一实例态守卫：挂起/暂停的实例上不可操作任何待办（spec §7）。"""
        instance = self._instance(ctx, task.instance_id)
        if instance.state == "suspended":
            raise TaskNotAvailableError("实例已挂起（Incident 待修复），不可操作")
        if instance.state == "paused":
            raise TaskNotAvailableError("实例已暂停，恢复后才可操作")

    def _clone_task(
        self,
        ctx: CommandContext,
        task: TaskRow,
        *,
        assignee: str,
        task_type: str | None = None,
        root: str | None = None,
    ) -> TaskRow:
        import uuid

        new_task = TaskRow(
            id=uuid.uuid4().hex,
            instance_id=task.instance_id,
            token_id=task.token_id,
            activity_code=task.activity_code,
            name=task.name,
            assignee=assignee,
            task_type=task_type or task.task_type,
            context_payload=dict(task.context_payload),
            root_task_id=root,
        )
        ctx.session.add(new_task)
        ctx.session.flush()
        self._emit_task_event(ctx, DomainEvent.TASK_CREATED, new_task, assignee)
        return new_task

    def _task_info(self, task: TaskRow) -> TaskInfo:
        return TaskInfo(
            id=task.id,
            instance_id=task.instance_id,
            token_id=task.token_id,
            activity_code=task.activity_code,
            name=task.name,
            assignee=task.assignee,
            task_type=task.task_type,
            state=task.state,
            context_payload=task.context_payload,
            priority=task.priority,
            due_at=task.due_at,
            created_at=task.created_at,
        )

    def _emit_task_event(
        self, ctx: CommandContext, event: DomainEvent, task: TaskRow, actor: str
    ) -> None:
        kernel = self._engine.kernel_for(ctx)
        kernel.record_event(
            event, {"task_id": task.id, "assignee": task.assignee, "actor": actor}, task.instance_id
        )
