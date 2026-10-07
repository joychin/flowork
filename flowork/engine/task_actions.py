"""任务操作集（M3）：认领 / 转办 / 协办 / 传阅 / 加签 / 撤回 / 管理员操作。

以 Mixin 形式并入 TaskService（保持四服务 API：engine.task.*）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError, TaskNotAvailableError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import (
    CcItemRow,
    IdentityLinkRow,
    TaskHistoryRow,
    TaskRow,
    TokenRow,
)
from flowork.runtime.lifecycle import TokenState
from flowork.tasks.models import TaskInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow
    from flowork.persistence.orm import TaskRow as _TaskRowT

__all__ = ["CcItemInfo", "TaskOpsMixin"]

from datetime import datetime

from pydantic import BaseModel, ConfigDict


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

    # —— 转办 ——;

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

    # —— 协办 ——;

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

    # —— 传阅 ——;

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

    # —— 加签 ——;

    def add_countersigner(
        self, task_id: str, users: list[str], actor: str, comment: str = ""
    ) -> list[TaskInfo]:
        """加签：向当前令牌追加会签人（item_count 同步增加，新待办立即生成）。

        前加签/后加签在并签池中语义等价（均需完成）；串行场景按剩余顺序排队。
        """
        result: list[TaskInfo] = self._engine.execute(
            lambda ctx: self._add_countersigner(ctx, task_id, users, actor, comment)
        )
        return result

    def _add_countersigner(
        self, ctx: CommandContext, task_id: str, users: list[str], actor: str, comment: str
    ) -> list[TaskInfo]:
        from flowork.definitions.builtin import UserTaskSpec

        task = self._ready_task(ctx, task_id)
        token = ctx.session.get(TokenRow, task.token_id)
        assert token is not None
        # 名单口径对齐：节点原名单 ∪ 已有手动名单 ∪ 新人 → item_count 与可办名单一致
        kernel = self._engine.kernel_for(ctx)
        instance = self._instance(ctx, task.instance_id)
        ctx.touch(instance)
        node = kernel.definition_of(ctx.session, instance).node(task.activity_code)
        base_participants: list[str] = []
        if isinstance(node, UserTaskSpec):
            base_participants = ([node.assignee] if node.assignee else []) + list(node.assignees)
        existing = list((token.manual_participants or {}).get(task.activity_code, []))
        merged = list(dict.fromkeys([*base_participants, *existing, *users]))
        token.manual_participants[task.activity_code] = merged
        token.item_count = len(merged)
        ctx.session.flush()
        kernel = self._engine.kernel_for(ctx)
        kernel.record_event(
            "task_countersigner_added",
            {"task_id": task_id, "users": users, "actor": actor, "comment": comment},
            task.instance_id,
        )
        created = [self._clone_task(ctx, task, assignee=user) for user in users]
        return [self._task_info(t) for t in created]

    # —— 撤回 ——;

    def withdraw(self, task_id: str, actor: str, comment: str = "") -> TaskInfo:
        """撤回本人已办任务：取消其后已激活的下游令牌，并在原节点重建待办。"""
        result: TaskInfo = self._engine.execute(
            lambda ctx: self._withdraw(ctx, task_id, actor, comment)
        )
        return result

    def _withdraw(self, ctx: CommandContext, task_id: str, actor: str, comment: str) -> TaskInfo:
        from sqlalchemy import select

        history = ctx.session.scalars(
            select(TaskHistoryRow).where(TaskHistoryRow.task_id == task_id)
        ).first()
        if history is None or history.outcome != "agree" or history.acted_by != actor:
            raise TaskNotAvailableError(f"任务不可撤回（未办理/非本人/非同意）：{task_id}")
        instance = self._instance(ctx, history.instance_id)
        if instance.state != "running":
            raise TaskNotAvailableError("实例已结束，不可撤回")
        ctx.touch(instance)
        kernel = self._engine.kernel_for(ctx)
        # 取消该办理之后产生的全部未完令牌（简化：撤回即回退到当前节点）
        for token in kernel.tokens(ctx.session, instance.id, states=(TokenState.UNFINISHED.value,)):
            kernel.cancel_token(ctx.session, token)
        # 原节点重建：新令牌 + 原办理人待办
        definition = kernel.definition_of(ctx.session, instance)
        node = definition.node(history.activity_code)
        assert node is not None
        token = kernel.activate(
            ctx.session,
            instance,
            node,
            source_activity_code=None,
            manual_participants={node.code: [history.assignee]},
        )
        kernel.record_event(
            "task_withdrawn",
            {"task_id": task_id, "actor": actor, "comment": comment, "new_token": token.id},
            instance.id,
        )
        tasks = kernel.tasks(ctx.session, instance.id, token_id=token.id)
        return self._task_info(tasks[0])

    # —— 管理员操作 ——;

    def activate_node(
        self,
        instance_id: str,
        activity_code: str,
        actor: str,
        participants: list[str] | None = None,
    ) -> str:
        """管理员激活任意节点（可指定参与者），返回新令牌 ID。"""

        def _command(ctx: CommandContext) -> str:
            instance = self._instance(ctx, instance_id)
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            node = kernel.definition_of(ctx.session, instance).node(activity_code)
            assert node is not None, f"节点不存在：{activity_code}"
            manual = {activity_code: participants} if participants else None
            token = kernel.activate(ctx.session, instance, node, manual_participants=manual)
            kernel.record_event(
                "node_activated_by_admin",
                {"activity_code": activity_code, "actor": actor},
                instance_id,
            )
            return token.id

        return self._engine.execute(_command)

    def cancel_node(self, instance_id: str, activity_code: str, actor: str) -> None:
        """管理员取消指定节点的全部未完令牌。"""

        def _command(ctx: CommandContext) -> None:
            ctx.touch(self._instance(ctx, instance_id))
            kernel = self._engine.kernel_for(ctx)
            tokens = kernel.tokens(ctx.session, instance_id, states=(TokenState.UNFINISHED.value,))
            for token in tokens:
                if token.activity_code == activity_code:
                    kernel.cancel_token(ctx.session, token)
            kernel.record_event(
                "node_cancelled_by_admin",
                {"activity_code": activity_code, "actor": actor},
                instance_id,
            )

        self._engine.execute(_command)

    def finish_instance(self, instance_id: str, actor: str) -> None:
        """管理员强制完成实例（未完令牌置 finished）。"""

        def _command(ctx: CommandContext) -> None:
            instance = self._instance(ctx, instance_id)
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            for token in kernel.tokens(
                ctx.session, instance_id, states=(TokenState.UNFINISHED.value,)
            ):
                for task in kernel.tasks(ctx.session, instance_id, token_id=token.id):
                    kernel.archive_task(
                        ctx.session,
                        task,
                        outcome="auto_pass",
                        comment="实例强制完成",
                        actor=actor,
                    )
                kernel.finish_token(ctx.session, token)
            kernel.complete_instance(ctx.session, instance, "completed")
            kernel.record_event("process_finished_by_admin", {"actor": actor}, instance_id)

        self._engine.execute(_command)

    def remove_instance(self, instance_id: str, actor: str) -> None:
        """删除实例及其全部运行时数据（含身份关联/Job/Incident，不可恢复）。"""

        def _command(ctx: CommandContext) -> None:
            from sqlalchemy import delete, select

            from flowork.persistence.orm import (
                EventOutboxRow,
                IncidentRow,
                JobRow,
                ProcessInstanceRow,
            )

            # 身份关联经 task_id 挂靠，先于任务删除
            ctx.session.execute(
                delete(IdentityLinkRow).where(
                    IdentityLinkRow.task_id.in_(
                        select(TaskRow.id).where(TaskRow.instance_id == instance_id)
                    )
                )
            )
            for row_type in (TaskRow, TaskHistoryRow, TokenRow, CcItemRow, JobRow):
                ctx.session.execute(delete(row_type).where(row_type.instance_id == instance_id))
            ctx.session.execute(
                delete(EventOutboxRow).where(EventOutboxRow.instance_id == instance_id)
            )
            ctx.session.execute(delete(IncidentRow).where(IncidentRow.instance_id == instance_id))
            instance = ctx.session.get(ProcessInstanceRow, instance_id)
            if instance is None:
                raise InstanceNotFoundError(f"实例不存在：{instance_id}")
            ctx.session.delete(instance)

        self._engine.execute(_command)

    # —— 共享助手 ——

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

    def _emit_task_event(
        self, ctx: CommandContext, event: DomainEvent, task: TaskRow, actor: str
    ) -> None:
        kernel = self._engine.kernel_for(ctx)
        kernel.record_event(
            event, {"task_id": task.id, "assignee": task.assignee, "actor": actor}, task.instance_id
        )
