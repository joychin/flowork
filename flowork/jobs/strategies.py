"""超时策略执行面（JobScheduler 组合件，入口类不变）。

从 scheduler.py 拆出（单文件 ≤300 行红线）；JobScheduler 经 Mixin 组合
获得本面：实例时限（mark/cancel/terminate）、任务超时（notify/auto_approve/
auto_reject/reassign/escalate）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.engine.kernel import EngineExecutionError
from flowork.engine.ops import CommandContext
from flowork.errors import TaskNotAvailableError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import JobRow, ProcessInstanceRow, TaskRow
from flowork.runtime.lifecycle import (
    FROZEN_INSTANCE_STATES,
    TERMINAL_INSTANCE_STATES,
    TokenState,
)

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["DeadlineStrategiesMixin"]


class DeadlineStrategiesMixin:
    """到期策略执行；宿主 JobScheduler 提供 _engine（kernel/task/config 取道）。"""

    _engine: ProcessEngine

    def _apply_strategy(self, ctx: CommandContext, job: JobRow) -> None:
        """执行到期策略（同事务）；已被人工处理则按"跳过"结案（不重试不死信）。"""
        kernel = self._engine.kernel_for(ctx)
        kind = job.payload.get("kind", "task_deadline")
        if kind == "instance_deadline":
            self._apply_instance_deadline(ctx, job, kernel)
            return
        if kind == "wait_timeout":
            self._apply_wait_timeout(ctx, job, kernel)
            return
        strategy = job.payload.get("strategy", "notify")
        task_id = job.payload.get("task_id")
        task = ctx.session.get(TaskRow, task_id) if task_id else None
        if strategy == "notify":
            instance = ctx.session.get(ProcessInstanceRow, job.instance_id)
            if instance is not None and instance.state in FROZEN_INSTANCE_STATES:
                # 冻结推迟（review 2026-10-13 P2-11）：notify 原是唯一不查冻结的
                # 策略，竞态窗插队的暂停实例会收到预警——与其他策略同款推迟
                job.state = "scheduled"
                job.result_payload = {**(job.result_payload or {}), "deferred": instance.state}
                return
            if task_id and (task is None or task.state != "ready"):
                # 等待已被终结（auto_approve/reject 先行）：预警不再触发（spec §5.1）
                job.result_payload = {
                    **(job.result_payload or {}),
                    "skipped": "task already finished",
                }
                return
            kernel.record_event(
                DomainEvent.TIMEOUT_WARNED,
                {
                    "job_id": job.id,
                    "strategy": strategy,
                    "task_id": task_id,  # B4：预警定位到任务（修前只有 job_id）
                    "activity_code": task.activity_code if task is not None else None,
                },
                job.instance_id,
            )
            return
        if not task_id:
            return
        if task is None or task.state != "ready":
            job.result_payload = {**(job.result_payload or {}), "skipped": "task already finished"}
            return
        if task.assignee == "":
            # 候选任务未认领的超时兜底（P1-4）：图纸配置的节点级 SLA 不因候选
            # 池积压而空洞——system:timeout 代认领后走统一执行路径（系统代管
            # claim→act 全程归因可查）。修前一次性 Job 被静默 skip，四策略全失效。
            task.assignee = "system:timeout"
            ctx.session.flush()
        try:
            if strategy in ("reassign", "escalate"):
                self._execute_handover(ctx, job, task, strategy)
            else:
                self._execute_strategy(ctx, strategy, task_id)
        except TaskNotAvailableError as exc:
            instance = ctx.session.get(ProcessInstanceRow, job.instance_id)
            if instance is not None and instance.state in FROZEN_INSTANCE_STATES:
                # collect→claim 竞态窗插队的冻结（P1-12）：不消费，解冻后触发
                job.state = "scheduled"
                job.result_payload = {**(job.result_payload or {}), "deferred": instance.state}
                return
            if self._consult_blocked(ctx, task):
                # 咨询软挂起期（P2-12）：主任务动作被拦但「时钟照走」＝推迟——
                # 意见交回后策略如期触发（修复前 Job 被消费，咨询后零时限）
                job.state = "scheduled"
                job.result_payload = {**(job.result_payload or {}), "deferred": "consult_open"}
                return
            # 如实记因（code-review 2026-10 P0-5）：此前一律写「task already
            # finished」——不可办场景被误标
            job.result_payload = {**(job.result_payload or {}), "skipped": str(exc)}

    def _apply_wait_timeout(self, ctx: CommandContext, job: JobRow, kernel: Any) -> None:
        """等待节点到时（spec §7.2）：订阅置 elapsed 留痕 → timer_fired → 推进。

        竞速仲裁零新代码：命中先落则订阅已 matched、令牌已完结——按跳过结案
        （不重试不死信）；到时先落则订阅行置 elapsed（waiting 反查只看 open、
        防重只看 matched，两处消费方不受影响），finish_wait 走 elapsed 路由。
        纯延时模式无订阅行，直接推进。
        """
        from sqlalchemy import update

        from flowork.persistence.orm import MessageSubscriptionRow, TokenRow

        token_id = job.payload.get("token_id")
        token = ctx.session.get(TokenRow, token_id) if token_id else None
        if token is None or token.state != TokenState.UNFINISHED.value:
            job.result_payload = {**(job.result_payload or {}), "skipped": "wait already resolved"}
            return
        instance = ctx.session.get(ProcessInstanceRow, job.instance_id)
        if instance is None or instance.state in TERMINAL_INSTANCE_STATES:
            job.result_payload = {**(job.result_payload or {}), "skipped": "instance finished"}
            return
        if instance.state in FROZEN_INSTANCE_STATES:
            # 冻结期（P1-12 collect→claim 竞态窗）：不消费，解冻后触发——
            # 与 collect 过滤「冻结是推迟不是吞掉」同语义
            job.state = "scheduled"
            job.result_payload = {**(job.result_payload or {}), "deferred": instance.state}
            return
        ctx.touch(instance)
        kernel.record_event(
            DomainEvent.TIMER_FIRED,
            {
                "kind": "wait_timeout",
                "activity_code": job.payload.get("activity_code"),
                "after_minutes": job.payload.get("after_minutes"),
            },
            instance.id,
        )
        ctx.session.execute(
            update(MessageSubscriptionRow)
            .where(
                MessageSubscriptionRow.token_id == str(token_id),
                MessageSubscriptionRow.state == "open",
            )
            .values(state="elapsed")
        )
        from flowork.tasks.wait import finish_wait

        finish_wait(kernel, ctx.session, instance, token, result="elapsed")

    def _apply_instance_deadline(self, ctx: CommandContext, job: JobRow, kernel: Any) -> None:
        """实例时限到点（v2.2 spec §7）：mark 打标继续跑 / cancel 作废 / terminate 终止。"""
        strategy = job.payload.get("strategy", "mark")
        instance = ctx.session.get(ProcessInstanceRow, job.instance_id)
        if instance is None or instance.state in TERMINAL_INSTANCE_STATES:
            job.result_payload = {**(job.result_payload or {}), "skipped": "instance finished"}
            return  # 实例已完成则静默不触发
        if instance.state in FROZEN_INSTANCE_STATES:
            # 冻结期（P1-12）：不消费，解冻后触发（resume 会顺延 run_at）
            job.state = "scheduled"
            job.result_payload = {**(job.result_payload or {}), "deferred": instance.state}
            return
        ctx.touch(instance)
        kernel.record_event(
            DomainEvent.INSTANCE_DEADLINE_REACHED,
            {
                "strategy": strategy,
                "deadline_at": None
                if instance.deadline_at is None
                else instance.deadline_at.isoformat(),
            },
            instance.id,
        )
        if strategy == "mark":
            instance.deadline_marked = True
            return
        from flowork.engine.terminate_cascade import cascade_terminate

        end_status = "cancelled" if strategy == "cancel" else "terminated"
        cascaded = cascade_terminate(
            self._engine,
            ctx,
            instance,
            end_status=end_status,
            actor="system:deadline",
            reason="instance_deadline",
        )
        kernel.complete_instance(ctx.session, instance, end_status)
        # 终态事件与 runtime 路径同款（消费端统一感知实例因何终态）
        terminal_event = (
            DomainEvent.PROCESS_CANCELLED
            if strategy == "cancel"
            else DomainEvent.PROCESS_TERMINATED
        )
        kernel.record_event(
            terminal_event,
            {
                "actor": "system:deadline",
                "reason": "instance_deadline",
                "cascaded_instances": cascaded,
            },
            instance.id,
        )

    def _execute_handover(
        self, ctx: CommandContext, job: JobRow, task: TaskRow, strategy: str
    ) -> None:
        """reassign / escalate：两条都是**图纸给的 CEL 目标**（v2.3 第十四轮）。

        取人语义归宿主（spec §5.5 分工铁律）——引擎不含「上级」概念，escalate 的
        目标由 `escalate_to` 表达式给出（科层组织写 `'manager_of(task.assignee)'`）。
        两者共用求值契约（fire 时求值、task.* 快照注入，spec §4.2/C.4），差别只在
        审计事件：escalate 记 TASK_ESCALATED（升级语义），reassign 不记。
        """
        from flowork.expr.cel import FormulaError, evaluate_formula_value
        from flowork.expr.context import expression_context, expression_functions_of

        kernel = self._engine.kernel_for(ctx)
        field = "reassign_to" if strategy == "reassign" else "escalate_to"
        expression = str(job.payload.get(field) or "")
        if not expression:
            raise EngineExecutionError(f"{strategy} deadline 缺少目标（{field}，job={job.id}）")
        # fire 时求值（spec §4.2/C.4）：task.* 快照取当前任务行，活任务 outcome=null
        instance = ctx.session.get(ProcessInstanceRow, job.instance_id)
        if instance is None:
            raise EngineExecutionError(f"{strategy} 实例不存在（job={job.id}）")
        snapshot = {
            "id": task.id,
            "assignee": task.assignee,
            "outcome": None,
            "created_at": task.created_at,
            "deadline_at": task.due_at,  # 首条 deadline 到期时刻（未配＝null）
        }
        from flowork.tasks.facts import instance_facts

        variables = expression_context(
            instance.variables or {},
            start_user=instance.start_user,
            task=snapshot,
            instance=instance_facts(instance),
            now=self._engine.config.now(),
        )
        try:
            target = evaluate_formula_value(
                expression, variables, functions=expression_functions_of(self._engine.config)
            )
        except FormulaError as exc:
            raise EngineExecutionError(
                f"超时{strategy}公式求值失败（job={job.id}，{expression!r}）：{exc}"
            ) from exc
        if not isinstance(target, str) or not target:
            raise EngineExecutionError(
                f"超时{strategy}公式须返回单个非空人（job={job.id}，{expression!r} → {target!r}）"
            )
        if strategy != "reassign":
            kernel.record_event(
                DomainEvent.TASK_ESCALATED,
                {
                    "task_id": task.id,
                    "activity_code": task.activity_code,  # 2026-10-13 事件 review 复核批
                    "task_type": task.task_type,
                    "from": task.assignee,
                    "to": target,
                },
                job.instance_id,
            )
        self._engine.task._transfer(ctx, task.id, target, "system:timeout", f"超时{strategy}")

    def _execute_strategy(self, ctx: CommandContext, strategy: str, task_id: str) -> None:
        """对在办任务执行策略（任务不存在由调用方跳过）。"""
        task = self._engine.task
        if strategy == "auto_approve":
            task.complete_in_ctx(ctx, task_id, actor="system:timeout", comment="超时自动通过")
        elif strategy == "auto_reject":
            task.reject_in_ctx(ctx, task_id, actor="system:timeout", comment="超时自动驳回")
        else:  # pragma: no cover - 配置兜底
            raise ValueError(f"未知超时策略：{strategy}")

    @staticmethod
    def _consult_blocked(ctx: CommandContext, task: TaskRow) -> bool:
        """主任务是否处于咨询软挂起（与 task_consult.assert_no_open_consults 同查询——
        守卫与策略判定共用同源，防漂移）。"""
        from sqlalchemy import select

        root = task.root_task_id or task.id
        return (
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
