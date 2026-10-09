"""TaskService：待办任务的办理与查询（操作全集见各 Mixin：task_actions /
task_rework / task_admin / task_cc / tasks.reject_flow / tasks.anticipate）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.engine.context import RuntimeNodeContext
from flowork.engine.ops import CommandContext
from flowork.engine.task_actions import TaskOpsMixin
from flowork.engine.task_admin import TaskAdminMixin
from flowork.engine.task_cc import TaskCcMixin
from flowork.engine.task_rework import TaskReworkMixin
from flowork.errors import TaskNotAvailableError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import TaskHistoryRow, TaskRow
from flowork.tasks.anticipate import AnticipateMixin
from flowork.tasks.models import TaskCompleteResult, TaskInfo
from flowork.tasks.reject_flow import RejectMixin

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["TaskCompleteResult", "TaskService"]


class TaskService(
    TaskOpsMixin, TaskReworkMixin, TaskAdminMixin, TaskCcMixin, RejectMixin, AnticipateMixin
):
    """任务服务：办理 / 查询。所有操作带 actor 归因（人 or agent 可区分）。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine

    # —— 查询 ——

    def get_task(self, task_id: str) -> TaskInfo | None:
        def _command(ctx: CommandContext) -> Any:
            task = ctx.session.get(TaskRow, task_id)
            return self._task_info(task) if task else None

        result: TaskInfo | None = self._engine.execute(_command)
        return result

    def list_tasks(
        self,
        *,
        instance_id: str | None = None,
        assignee: str | None = None,
        state: str = "ready",
    ) -> list[TaskInfo]:
        """查询待办（默认 ready；state=None 查全部）。"""
        from sqlalchemy import select

        def _command(ctx: CommandContext) -> list[TaskInfo]:
            stmt = select(TaskRow)
            if instance_id:
                stmt = stmt.where(TaskRow.instance_id == instance_id)
            if assignee:
                stmt = stmt.where(TaskRow.assignee == assignee)
            if state:
                stmt = stmt.where(TaskRow.state == state)
            return [self._task_info(row) for row in ctx.session.scalars(stmt)]

        result: list[TaskInfo] = self._engine.execute(_command)
        return result

    # —— 办理 ——

    def complete(
        self,
        task_id: str,
        actor: str,
        *,
        comment: str = "",
        variables: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        as_agent: bool = False,
    ) -> TaskCompleteResult:
        """同意并提交任务。

        Args:
            task_id: 待办任务 ID。
            actor: 办理人（审计归因）。
            comment: 审批意见。
            variables: 本次提交的变量（合并进实例变量）。
            idempotency_key: 幂等键：重复提交同键返回首次结果，无副作用。
            as_agent: 是否由 AI agent 代办（审计区分）。
        """
        result: TaskCompleteResult = self._engine.execute(
            lambda ctx: self._complete(
                ctx, task_id, actor, comment, variables, idempotency_key, as_agent
            )
        )
        return result

    def complete_in_ctx(
        self,
        ctx: CommandContext,
        task_id: str,
        actor: str,
        *,
        comment: str = "",
        variables: dict[str, Any] | None = None,
        as_agent: bool = False,
    ) -> TaskCompleteResult:
        """同事务办理入口（供调度器等复合命令复用；不经 execute_command）。"""
        return self._complete(ctx, task_id, actor, comment, variables, None, as_agent)

    # —— 内部 ——

    def _complete(
        self,
        ctx: CommandContext,
        task_id: str,
        actor: str,
        comment: str,
        variables: dict[str, Any] | None,
        idempotency_key: str | None,
        as_agent: bool,
    ) -> TaskCompleteResult:
        task = ctx.session.get(TaskRow, task_id)
        if task is None or task.state != "ready":
            if idempotency_key:
                previous = self._find_history_by_key(ctx, idempotency_key)
                if previous is not None:
                    return self._result(ctx, previous.task_id, previous.outcome)
            raise TaskNotAvailableError(f"任务不可办理（不存在或已处理）：{task_id}")
        if task.task_type == "assist":
            raise TaskNotAvailableError("协办任务请用 complete_assist 完成")
        from flowork.tasks.actions import assert_action_allowed

        assert_action_allowed("complete", task)  # 动作表守卫（v2.3 A1：状态×类型×认领态）
        instance = self._instance(ctx, task.instance_id)
        self._claim_task_action(ctx, task, instance)  # 版本抢占+复验（关双 agree 竞态窗）
        # 变量留痕归因：本命令内的变量变化（任务 output + 后续钩子写）记 task_output
        ctx.write_source = "task_output"
        ctx.write_actor = actor
        ctx.write_as_agent = as_agent
        if instance.state == "suspended":
            raise TaskNotAvailableError("实例已挂起（Incident 待修复），不可办理")
        if instance.state == "paused":
            raise TaskNotAvailableError("实例已暂停，恢复后才可办理")
        if task.assignee == "":
            raise TaskNotAvailableError("候选任务需先 claim 认领")
        if variables:
            # input 契约边界②：任务提交 output 后变量全集仍须满足契约（spec §3.2）
            from flowork.definitions.contract import input_schema_of, validate_input_contract

            schema = input_schema_of(instance)
            if schema:
                merged = dict(instance.variables)
                merged.update(variables)
                validate_input_contract(schema, merged, boundary="task_output")
            instance.variables.update(variables)
        kernel = self._engine.kernel_for(ctx)
        from flowork.persistence.orm import TokenRow

        token = ctx.session.get(TokenRow, task.token_id)
        if token is None:  # pragma: no cover - 数据一致性保护
            raise TaskNotAvailableError(f"任务令牌不存在：{task.token_id}")
        token.approve_count += 1
        kernel.record_event(
            DomainEvent.TASK_COMPLETED,
            {"task_id": task.id, "assignee": task.assignee, "actor": actor, "outcome": "agree"},
            instance.id,
        )
        kernel.archive_task(
            ctx.session,
            task,
            outcome="agree",
            comment=comment,
            actor=actor,
            as_agent=as_agent,
            idempotency_key=idempotency_key,
        )
        self._cancel_assist_tasks(ctx, task)  # 协办随主任务终态同事务收口（spec §5.1）
        node = kernel.definition_of(ctx.session, instance).node(task.activity_code)
        assert node is not None, f"任务节点不存在：{task.activity_code}"
        behavior = kernel.behavior_of(node)
        node_ctx = RuntimeNodeContext(kernel, ctx.session, instance, node, token)
        behavior.on_task_completed(node_ctx, task.id, "agree")
        kernel.advance_after_task(ctx.session, instance, token)
        return self._result(ctx, task.id, "agree")

    def _result(self, ctx: CommandContext, task_id: str, outcome: str) -> TaskCompleteResult:
        """同事务内构造结果（避免嵌套会话读不到未提交数据）。"""
        from sqlalchemy import select

        history = ctx.session.scalars(
            select(TaskHistoryRow).where(TaskHistoryRow.task_id == task_id)
        ).first()
        if history is None:  # pragma: no cover - 归档后必然存在
            return TaskCompleteResult(task_id=task_id, outcome=outcome, instance_state="unknown")
        instance = self._instance(ctx, history.instance_id)
        next_tasks: list[TaskInfo] = []
        if instance.state == "running":
            rows = ctx.session.scalars(
                select(TaskRow).where(TaskRow.instance_id == instance.id, TaskRow.state == "ready")
            )
            next_tasks = [self._task_info(row) for row in rows]
        return TaskCompleteResult(
            task_id=task_id,
            outcome=outcome,
            instance_state=instance.state,
            next_tasks=next_tasks,
        )
