"""加签面（TaskService 组合件，入口 engine.task.add_countersigner 不变）。

加签人各占一票（spec §5.1）；重复加签整笔回拒；克隆任务独立满档
deadline；规模护栏与 assist/consult 同守。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.ops import CommandContext
from flowork.errors import TaskNotAvailableError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import (
    TaskRow,
    TokenRow,
)
from flowork.tasks.models import TaskInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["TaskCountersignAddMixin"]


class TaskCountersignAddMixin:
    """加签：向当前令牌追加会签人（新票人独立满档 SLA）。"""

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

        task = self._ready_task(ctx, task_id, action="add_countersigner")
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
                now=self._engine.config.now(),
            )
        existing = list((token.manual_participants or {}).get(task.activity_code, []))
        merged = list(dict.fromkeys([*base_participants, *existing, *users]))
        # 规模护栏：加签是调用方请求——超限拒绝整笔（回滚），实例不挂起；
        # limit 缺省 None＝不设防（内核不替宿主选数值）
        limit = self._engine.config.max_participants
        if limit is not None and len(merged) > limit:
            raise TaskNotAvailableError(
                f"节点 {task.activity_code} 加签后名单 {len(merged)} 人超过上限 {limit}"
                "（EngineConfig.max_participants）——拒绝而非截断"
            )
        token.manual_participants[task.activity_code] = merged
        token.item_count = len(merged)
        ctx.session.flush()
        # kernel 复用上方实例：重复
        # kernel_for 会重解析定义 JSON，且后续往 Kernel 加实例内状态即踩坑
        kernel.record_event(
            DomainEvent.TASK_COUNTERSIGNER_ADDED,
            {
                "task_id": task_id,
                "activity_code": task.activity_code,
                "users": users,
                "actor": actor,
                "comment": comment,
            },
            task.instance_id,
        )
        root = task.root_task_id or task.id
        # 重复加签守卫：同令牌已有 ready 任务的人
        # 再被加签＝一人两票（item_count 不随重复增加），出口提前达成、其余
        # 成员被 auto_pass 从未参与。整笔回拒不部分生效（对齐 max_participants
        # 「拒绝而非截断」口径）；user: 前缀写法与裸 id 视同一人。
        from sqlalchemy import select

        from flowork.persistence.orm import TaskRow as _TaskRow
        from flowork.tasks.candidates import person_key

        active = {
            person_key(assignee)
            for assignee in ctx.session.scalars(
                select(_TaskRow.assignee).where(
                    _TaskRow.token_id == task.token_id, _TaskRow.state == "ready"
                )
            )
        }
        duplicates = sorted({user for user in users if person_key(user) in active})
        if duplicates:
            raise TaskNotAvailableError(
                f"加签人已在会签名单（同令牌在办）：{'、'.join(duplicates)}"
            )
        created = [
            self._clone_task(ctx, task, assignee=user, operator=actor, root=root) for user in users
        ]
        # 加签人同样进 SLA：克隆任务此前零 timer 零
        # due_at——加签人不办则会签节点无限期悬挂。新票人按并行会签同款独立满档。
        if isinstance(node, UserTaskSpec):
            from flowork.engine.context import RuntimeNodeContext
            from flowork.tasks.deadlines import schedule_deadline_jobs

            node_ctx = RuntimeNodeContext(kernel, ctx.session, instance, node, None)
            for new_task in created:
                schedule_deadline_jobs(node_ctx, node, new_task.id)
        return [self._task_info(t) for t in created]
