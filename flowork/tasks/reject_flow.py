"""驳回回流（TaskService 组合件，入口 engine.task.reject 不变）。

从 task_service.py 拆出（单文件 ≤300 行回归），镜像 anticipate.py 放法。
语义（对齐 对标产品，实现为追加式）：
- 单人节点：一票即回退；多实例节点：达到 disapprove_exit（默认 1 票）回退。
- 目标节点 = 显式 target > 节点配置 reject_target > 来源节点。
- 回退 = 新令牌（reject_flag=True）+ 为目标节点原办理人生成新待办
  （cause_token_id 溯源；历史只追加不修改）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.engine.ops import CommandContext
from flowork.errors import TaskNotAvailableError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import TaskHistoryRow, TaskRow
from flowork.runtime.lifecycle import TokenState
from flowork.tasks.models import TaskCompleteResult

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["RejectMixin"]


class RejectMixin:
    """驳回面；宿主 TaskService 提供 _engine/_instance/_result。"""

    _engine: ProcessEngine

    def _instance(self, ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
        """实例加载由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _claim_task_action(
        self, ctx: CommandContext, task: TaskRow, instance: ProcessInstanceRow
    ) -> None:
        """动作抢占咽喉（版本守卫+复验）由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _cancel_assist_tasks(self, ctx: CommandContext, task: TaskRow) -> None:
        """协办随主任务收口由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _result(self, ctx: CommandContext, task_id: str, outcome: str) -> TaskCompleteResult:
        """同事务结果构造由 TaskService 提供。"""
        raise NotImplementedError

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
        """驳回任务：结束当前节点（否决出口）并回退到目标节点。"""
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

        task = ctx.session.get(TaskRow, task_id)
        if task is None or task.state != "ready":
            raise TaskNotAvailableError(f"任务不可驳回（不存在或已处理）：{task_id}")
        from flowork.tasks.actions import assert_action_allowed

        assert_action_allowed("reject", task)  # 动作表守卫（v2.3 A1：状态×类型×认领态）
        instance = self._instance(ctx, task.instance_id)
        self._claim_task_action(ctx, task, instance)  # 版本抢占+复验（关双 outcome 竞态窗）
        # 变量留痕归因：驳回携带的 variables 与驳回复活路径写变量记 task_reject
        ctx.write_source = "task_reject"
        ctx.write_actor = actor
        ctx.write_as_agent = as_agent
        if instance.state == "suspended":
            raise TaskNotAvailableError("实例已挂起（Incident 待修复），不可驳回")
        if instance.state == "paused":
            raise TaskNotAvailableError("实例已暂停，恢复后才可驳回")
        if variables:
            # input 契约边界（spec §3.2）：驳回携带的 variables 同为任务提交侧输出
            from flowork.definitions.contract import input_schema_of, validate_input_contract

            schema = input_schema_of(instance)
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
        self._cancel_assist_tasks(ctx, task)  # 协办随主任务终态同事务收口（spec §5.1）
        kernel.record_event(
            DomainEvent.TASK_REJECTED,
            {"task_id": task.id, "assignee": task.assignee, "actor": actor},
            instance.id,
        )

        node = kernel.definition_of(ctx.session, instance).node(task.activity_code)
        assert node is not None
        spec = node if isinstance(node, UserTaskSpec) else None
        mi = spec.multi_instance if spec else None
        # 出口判定（单轨 CEL）：会签未配＝全员否决；非会签＝任一否决即出口（原 >=1 语义）
        from flowork.expr.context import expression_functions_of
        from flowork.tasks.countersign import exit_reached
        from flowork.tasks.facts import instance_facts

        expr = mi.disapprove_exit if mi is not None else "task.disapprove_count >= 1"
        exit_hit = exit_reached(
            expr,
            side="disapprove",
            item_count=token.item_count,
            approve_count=token.approve_count,
            disapprove_count=token.disapprove_count,
            variables=instance.variables,
            start_user=instance.start_user,
            functions=expression_functions_of(self._engine.config),
            instance=instance_facts(instance),
            now=self._engine.config.now(),  # 第十五轮时钟统一：与办理侧出口同一时钟
        )
        if not exit_hit:
            from flowork.engine.context import RuntimeNodeContext

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

        # 复活圈定「最近一轮」（spec §5.1 v2.3）：目标节点最近一个已完结令牌
        # （token_no 最大、非 unfinished）名下的办理人——多轮驳回名单不随轮次膨胀；
        # 与主流「重入按定义重新解析」的刻意差异＝复活原办理人（国内审批语义）。
        latest_settled = (
            select(_TokenRow.id)
            .where(
                _TokenRow.instance_id == instance.id,
                _TokenRow.activity_code == reject_target,
                _TokenRow.state != TokenState.UNFINISHED.value,
            )
            .order_by(_TokenRow.token_no.desc())
            .limit(1)
        )
        stmt = select(TaskHistoryRow).where(
            TaskHistoryRow.instance_id == instance.id,
            TaskHistoryRow.activity_code == reject_target,
            TaskHistoryRow.outcome != "cancelled",
        )
        latest_token_id = ctx.session.scalars(latest_settled).first()
        if latest_token_id is not None:
            stmt = stmt.where(TaskHistoryRow.token_id == latest_token_id)
        history_rows = ctx.session.scalars(stmt)
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

    def _find_history_by_key(self, ctx: CommandContext, key: str) -> TaskHistoryRow | None:
        from sqlalchemy import select

        return ctx.session.scalars(
            select(TaskHistoryRow).where(TaskHistoryRow.idempotency_key == key)
        ).first()
