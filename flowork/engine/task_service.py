"""TaskService：待办任务的办理与查询（操作全集见 task_actions.TaskOpsMixin）。"""

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
from flowork.runtime.lifecycle import TokenState
from flowork.tasks.anticipate import AnticipateMixin
from flowork.tasks.models import TaskInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["TaskCompleteResult", "TaskService"]

from pydantic import BaseModel, ConfigDict


class TaskCompleteResult(BaseModel):
    """complete/reject 的返回：结果 + 下一步可办任务（行动提示式返回）。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    outcome: str
    instance_state: str
    next_tasks: list[TaskInfo] = []


def _input_schema_of(instance: Any) -> dict[str, Any] | None:
    """实例钉住的 input 契约（快照原样字段，不做 pydantic 解析）。"""
    import json as _json

    try:
        return (_json.loads(instance.definition_snapshot) or {}).get("input")
    except (ValueError, TypeError):
        return None


class TaskService(TaskOpsMixin, TaskReworkMixin, TaskAdminMixin, TaskCcMixin, AnticipateMixin):
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

    def reject_in_ctx(
        self,
        ctx: CommandContext,
        task_id: str,
        actor: str,
        *,
        comment: str = "",
        target: str | None = None,
        as_agent: bool = False,
    ) -> TaskCompleteResult:
        """同事务驳回入口（供调度器等复合命令复用）。"""
        return self._reject(ctx, task_id, actor, comment, target, None, as_agent)

    def reject(
        self,
        task_id: str,
        actor: str,
        *,
        comment: str = "",
        target: str | None = None,
        variables: dict[str, Any] | None = None,
        as_agent: bool = False,
    ) -> TaskCompleteResult:
        """驳回任务：结束当前节点（否决出口）并回退到目标节点。

        语义（对齐 对标产品，实现为追加式）：
        - 单人节点：一票即回退；多实例节点：达到 disapprove_exit（默认 1 票）回退。
        - 目标节点 = 显式 target > 节点配置 reject_target > 来源节点。
        - 回退 = 新令牌（reject_flag=True）+ 为目标节点原办理人生成新待办
          （cause_token_id 溯源；历史只追加不修改）。
        """
        return self._engine.execute(
            lambda ctx: self._reject(ctx, task_id, actor, comment, target, variables, as_agent)
        )

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
        instance = self._instance(ctx, task.instance_id)
        ctx.touch(instance)
        if instance.state == "suspended":
            raise TaskNotAvailableError("实例已挂起（Incident 待修复），不可办理")
        if instance.state == "paused":
            raise TaskNotAvailableError("实例已暂停，恢复后才可办理")
        if task.assignee == "":
            raise TaskNotAvailableError("候选任务需先 claim 认领")
        if variables:
            # input 契约边界②：任务提交 output 后变量全集仍须满足契约（spec §3.2）
            from flowork.definitions.contract import validate_input_contract

            schema = _input_schema_of(instance)
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
        node = kernel.definition_of(ctx.session, instance).node(task.activity_code)
        assert node is not None, f"任务节点不存在：{task.activity_code}"
        behavior = kernel.behavior_of(node)
        node_ctx = RuntimeNodeContext(kernel, ctx.session, instance, node, token)
        behavior.on_task_completed(node_ctx, task.id, "agree")
        kernel.advance_after_task(ctx.session, instance, token)
        return self._result(ctx, task.id, "agree")

    def _reject(
        self,
        ctx: CommandContext,
        task_id: str,
        actor: str,
        comment: str,
        target: str | None,
        variables: dict[str, Any] | None,
        as_agent: bool,
    ) -> TaskCompleteResult:
        from flowork.definitions.builtin import UserTaskSpec
        from flowork.engine.kernel import RejectTargetMissing
        from flowork.persistence.orm import TokenRow as _TokenRow
        from flowork.tasks.countersign import evaluate_exit

        task = ctx.session.get(TaskRow, task_id)
        if task is None or task.state != "ready":
            raise TaskNotAvailableError(f"任务不可驳回（不存在或已处理）：{task_id}")
        instance = self._instance(ctx, task.instance_id)
        ctx.touch(instance)
        if instance.state == "suspended":
            raise TaskNotAvailableError("实例已挂起（Incident 待修复），不可驳回")
        if instance.state == "paused":
            raise TaskNotAvailableError("实例已暂停，恢复后才可驳回")
        if variables:
            # input 契约边界（spec §3.2）：驳回携带的 variables 同为任务提交侧输出
            from flowork.definitions.contract import validate_input_contract

            schema = _input_schema_of(instance)
            if schema:
                merged = dict(instance.variables)
                merged.update(variables)
                validate_input_contract(schema, merged, boundary="task_output")
            instance.variables.update(variables)
        kernel = self._engine.kernel_for(ctx)
        token = ctx.session.get(_TokenRow, task.token_id)
        assert token is not None
        token.disapprove_count += 1
        kernel.archive_task(
            ctx.session, task, outcome="reject", comment=comment, actor=actor, as_agent=as_agent
        )
        kernel.record_event(
            DomainEvent.TASK_REJECTED,
            {"task_id": task.id, "assignee": task.assignee, "actor": actor},
            instance.id,
        )

        node = kernel.definition_of(ctx.session, instance).node(task.activity_code)
        assert node is not None
        spec = node if isinstance(node, UserTaskSpec) else None
        disapprove_exit = (
            spec.multi_instance.disapprove_exit if spec and spec.multi_instance else ">=1"
        )
        exit_reached = evaluate_exit(disapprove_exit, token.item_count, token.disapprove_count)
        if not exit_reached:
            node_ctx = RuntimeNodeContext(kernel, ctx.session, instance, node, token)
            kernel.behavior_of(node).on_task_completed(node_ctx, task.id, "reject")
            kernel.advance_after_task(ctx.session, instance, token)
            return self._result(ctx, task.id, "reject")

        reject_target = (
            target or (spec.reject_target if spec else None) or token.source_activity_code
        )
        if not reject_target:
            raise RejectTargetMissing(f"任务 {task_id} 无可用驳回目标")
        token.approval_exit = "reject"
        kernel.finish_token(ctx.session, token, approval_exit="reject")
        # 并行场景：驳回归零——取消其余未完令牌（兄弟分支不再继续）
        unfinished = (TokenState.UNFINISHED.value,)
        for other in kernel.tokens(ctx.session, instance.id, states=unfinished):
            if other.id != token.id:
                kernel.cancel_token(ctx.session, other)
        for pending in kernel.tasks(ctx.session, instance.id, token_id=token.id):
            kernel.archive_task(
                ctx.session,
                pending,
                outcome="auto_pass",
                comment="节点已否决",
                actor="system",
                cause_token_id=token.id,
            )
        kernel.record_event(DomainEvent.NODE_EXITED, {"activity_code": node.code}, instance.id)
        from sqlalchemy import select

        history_rows = ctx.session.scalars(
            select(TaskHistoryRow).where(
                TaskHistoryRow.instance_id == instance.id,
                TaskHistoryRow.activity_code == reject_target,
                TaskHistoryRow.outcome != "cancelled",
            )
        )
        handlers = [row.assignee for row in history_rows]
        manual = {reject_target: list(dict.fromkeys(handlers))} if handlers else None
        definition = kernel.definition_of(ctx.session, instance)
        target_node = definition.node(reject_target)
        assert target_node is not None, f"驳回目标节点不存在：{reject_target}"
        kernel.activate(
            ctx.session,
            instance,
            target_node,
            source_activity_code=node.code,
            reject_flag=True,
            manual_participants=manual,
        )
        return self._result(ctx, task.id, "reject")

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

    def _find_history_by_key(self, ctx: CommandContext, key: str) -> TaskHistoryRow | None:
        from sqlalchemy import select

        return ctx.session.scalars(
            select(TaskHistoryRow).where(TaskHistoryRow.idempotency_key == key)
        ).first()
