"""TaskService：待办任务的操作面（complete 为 M1 主干，其余操作随 M2/M3 增补）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.engine.context import RuntimeNodeContext
from flowork.engine.kernel import Kernel
from flowork.engine.ops import CommandContext
from flowork.errors import TaskNotAvailableError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import TaskHistoryRow, TaskRow
from flowork.runtime.lifecycle import TokenState
from flowork.tasks.models import InstanceInfo, TaskHistoryInfo, TaskInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["TaskCompleteResult", "TaskService"]


from pydantic import BaseModel, ConfigDict


class TaskCompleteResult(BaseModel):
    """complete/reject 的返回：结果 + 下一步可办任务（行动提示式返回）。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    outcome: str
    instance_state: str
    next_tasks: list[TaskInfo] = []


class TaskService:
    """任务服务：办理 / 查询。所有操作带 actor 归因（人 or agent 可区分）。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine

    # —— 查询 ——

    def get_task(self, task_id: str) -> TaskInfo | None:
        def _command(ctx: CommandContext) -> Any:
            task = ctx.session.get(TaskRow, task_id)
            return _to_info(task) if task else None

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

        def _command(ctx: CommandContext) -> Any:
            stmt = select(TaskRow)
            if instance_id:
                stmt = stmt.where(TaskRow.instance_id == instance_id)
            if assignee:
                stmt = stmt.where(TaskRow.assignee == assignee)
            if state:
                stmt = stmt.where(TaskRow.state == state)
            return [_to_info(row) for row in ctx.session.scalars(stmt)]

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

        Args:
            task_id: 待驳回任务 ID。
            actor: 驳回人。
            comment: 驳回意见。
            target: 驳回目标节点编码（缺省按上述优先级推导）。
            variables: 随驳回合并的变量。
            as_agent: 是否 agent 代办。
        """
        return self._engine.execute(
            lambda ctx: self._reject(ctx, task_id, actor, comment, target, variables, as_agent)
        )

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
        if variables:
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

        # 出口达成：结束当前节点（否决）并回退
        reject_target = (
            target or (spec.reject_target if spec else None) or token.source_activity_code
        )
        if not reject_target:
            raise RejectTargetMissing(f"任务 {task_id} 无可用驳回目标")
        token.approval_exit = "reject"
        kernel.finish_token(ctx.session, token, approval_exit="reject")
        remaining = kernel.tasks(ctx.session, instance.id, token_id=token.id)
        for pending in remaining:
            kernel.archive_task(
                ctx.session,
                pending,
                outcome="auto_pass",
                comment="节点已否决",
                actor="system",
                cause_token_id=token.id,
            )
        kernel.record_event(DomainEvent.NODE_EXITED, {"activity_code": node.code}, instance.id)
        # 回退目标的原办理人重新待办（无历史办理人则按节点配置解析）
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
        instance = self._instance(ctx, task.instance_id)
        if variables:
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
        self._archive_task(
            ctx,
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
        if behavior is not None:
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
            next_tasks = [_to_info(row) for row in rows]
        return TaskCompleteResult(
            task_id=task_id,
            outcome=outcome,
            instance_state=instance.state,
            next_tasks=next_tasks,
        )

    def _archive_task(
        self,
        ctx: CommandContext,
        task: TaskRow,
        *,
        outcome: str,
        comment: str,
        actor: str,
        as_agent: bool = False,
        idempotency_key: str | None = None,
        cause_token_id: str | None = None,
    ) -> None:

        kernel = self._engine.kernel_for(ctx)
        kernel.archive_task(
            ctx.session,
            task,
            outcome=outcome,
            comment=comment,
            actor=actor,
            as_agent=as_agent,
            idempotency_key=idempotency_key,
            cause_token_id=cause_token_id,
        )

    def _find_history_by_key(self, ctx: CommandContext, key: str) -> TaskHistoryRow | None:
        from sqlalchemy import select

        from flowork.persistence.orm import TaskHistoryRow

        return ctx.session.scalars(
            select(TaskHistoryRow).where(TaskHistoryRow.idempotency_key == key)
        ).first()

    def _instance(self, ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
        from flowork.persistence.orm import ProcessInstanceRow as _Row

        instance = ctx.session.get(_Row, instance_id)
        if instance is None:  # pragma: no cover
            raise TaskNotAvailableError(f"实例不存在：{instance_id}")
        assert instance is not None
        return instance


def _to_info(task: TaskRow) -> TaskInfo:
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
        created_at=task.created_at,
    )


def _history_to_info(row: TaskHistoryRow) -> TaskHistoryInfo:
    return TaskHistoryInfo(
        id=row.id,
        task_id=row.task_id,
        instance_id=row.instance_id,
        token_id=row.token_id,
        activity_code=row.activity_code,
        name=row.name,
        assignee=row.assignee,
        task_type=row.task_type,
        state="completed",
        context_payload=row.context_payload,
        created_at=row.created_at,
        outcome=row.outcome,
        comment=row.comment,
        acted_by=row.acted_by,
        acted_as_agent=row.acted_as_agent,
        cause_token_id=row.cause_token_id,
    )


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex


def _cost_seconds(created_at: object) -> float:
    import datetime as _dt

    if created_at is None:
        return 0.0
    delta = _dt.datetime.now() - created_at  # type: ignore[operator]
    return float(delta.total_seconds())


_ = TokenState, InstanceInfo, _history_to_info, Kernel  # M2/M3 使用的导出占位
