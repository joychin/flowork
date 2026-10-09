"""加签与撤回面（TaskService 组合件，入口 engine.task.* 不变）。

从 task_actions.py 拆出（单文件 ≤300 行回归）：
add_countersigner / withdraw。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.ops import CommandContext
from flowork.errors import TaskNotAvailableError
from flowork.persistence.orm import TaskHistoryRow, TokenRow
from flowork.runtime.lifecycle import TokenState
from flowork.tasks.models import TaskInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow, TaskRow

__all__ = ["TaskReworkMixin"]


class TaskReworkMixin:
    """加签/撤回；宿主 TaskService 提供 _engine 与共享助手（_ready_task/_clone_task 等）。"""

    _engine: ProcessEngine

    def _instance(self, ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
        """实例加载由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _ready_task(
        self, ctx: CommandContext, task_id: str, expected_type: str | None = None
    ) -> TaskRow:
        """待办加载与守卫由 TaskOpsMixin 提供。"""
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
            # 名单口径对齐：节点表达式先解析为具体人（manual_participants 存人，不存表达式原文）
            from flowork.expr.context import expression_functions_of
            from flowork.participants.resolver import resolve_subjects
            from flowork.tasks.facts import attach_node, instance_facts, with_prev_task

            expressions = ([node.assignee] if node.assignee else []) + list(node.assignees)
            base_participants = resolve_subjects(
                expressions,
                with_prev_task(ctx.session, instance.id, instance.variables or {}),
                instance.start_user,
                attach_node(expression_functions_of(self._engine.config), ctx.session, instance.id),
                label=task.activity_code,
                instance=instance_facts(instance),
            )
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
