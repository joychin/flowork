"""实例生命周期与故障处置（v2 spec §7/§8）：pause/resume/terminate/cancel、
Incident 五动作、move_token 管理员手术。

RuntimeService 经 mixin 组合（engine.runtime 入口不变）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import ProcessInstanceRow
from flowork.tasks.models import InstanceInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["InstanceLifecycleMixin"]


class InstanceLifecycleMixin:
    """RuntimeService 的生命周期/故障处置面。"""

    _engine: ProcessEngine

    def pause_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """暂停实例（spec §7）：任务不可办、timer 冻结；恢复后按自然时长顺延。"""

        def _command(ctx: CommandContext) -> Any:
            instance = _load(ctx, instance_id)
            if instance.state != "running":
                raise InstanceNotFoundError(
                    f"实例 {instance_id} 状态 {instance.state!r} 不可暂停（须 running）"
                )
            ctx.touch(instance)
            instance.state = "paused"
            instance.paused_at = self._engine.config.now()
            kernel = self._engine.kernel_for(ctx)
            kernel.record_event(
                DomainEvent.PROCESS_PAUSED, {"actor": actor, "reason": reason}, instance_id
            )

        self._engine.execute(_command)

    def resume_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """恢复暂停实例：未完 timer 的 run_at 顺延暂停时长（工作日历默认自然时间）。"""

        def _command(ctx: CommandContext) -> Any:
            from datetime import timedelta

            from flowork.persistence.orm import JobRow

            instance = _load(ctx, instance_id)
            if instance.state != "paused":
                raise InstanceNotFoundError(
                    f"实例 {instance_id} 状态 {instance.state!r} 不可恢复（须 paused）"
                )
            ctx.touch(instance)
            now = self._engine.config.now()
            paused_minutes = 0
            if instance.paused_at is not None:
                paused_minutes = int((now - instance.paused_at).total_seconds() // 60)
            instance.state = "running"
            instance.paused_at = None
            for job in ctx.session.scalars(
                select(JobRow).where(JobRow.instance_id == instance_id, JobRow.state == "scheduled")
            ):
                job.run_at = job.run_at + timedelta(minutes=paused_minutes)
            kernel = self._engine.kernel_for(ctx)
            kernel.record_event(
                DomainEvent.PROCESS_RESUMED,
                {"actor": actor, "reason": reason, "deferred_minutes": paused_minutes},
                instance_id,
            )
            # 暂停期间到达的消息（落缓冲）反查：open 订阅补消费（spec §7.2）。
            from flowork.engine.message_flow import retro_poll_instance

            retro_poll_instance(ctx, self._engine, instance)

            # 暂停期间到达的子实例完结补推进（code-review 2026-10 P0-2）：
            # resume_parent_if_waiting 在父 paused 时直接 return、不留补偿——
            # 恢复后数据驱动补一轮：逐未完令牌问行为 try_complete（kind 无关，
            # 等待中的 CallActivity 子实例已全部结算即放行；user_task/wait 等
            # 行为自判 no-op）。
            from flowork.runtime.lifecycle import TokenState

            kernel = self._engine.kernel_for(ctx)
            for token in kernel.tokens(
                ctx.session, instance_id, states=(TokenState.UNFINISHED.value,)
            ):
                kernel.advance_after_task(ctx.session, instance, token)

        self._engine.execute(_command)

    def terminate_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """终止实例（spec §7）：管理员强杀——整树撤令牌与待办，终态 terminated。

        第十四轮：级联未结算子实例（含暂停/挂起中的）同置 terminated
        （terminate_cascade——孤儿待办收口，对齐 Camunda/Zeebe 子树终止）。
        """

        def _command(ctx: CommandContext) -> Any:
            instance = _load(ctx, instance_id)
            if instance.state in ("completed", "cancelled", "terminated"):
                raise InstanceNotFoundError(f"实例 {instance_id} 已终态（{instance.state}）")
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            from flowork.engine.terminate_cascade import cascade_terminate

            cascaded = cascade_terminate(
                self._engine, ctx, instance, end_status="terminated", actor=actor, reason=reason
            )
            kernel.complete_instance(ctx.session, instance, "terminated")
            kernel.record_event(
                DomainEvent.PROCESS_TERMINATED,
                {"actor": actor, "reason": reason, "cascaded_instances": cascaded},
                instance_id,
            )

        self._engine.execute(_command)

    def cancel_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """取消实例：整树撤销未完令牌与待办（子实例级联 cancelled，第十四轮）。"""

        def _command(ctx: CommandContext) -> Any:
            instance = _load(ctx, instance_id)
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            from flowork.engine.terminate_cascade import cascade_terminate

            cascaded = cascade_terminate(
                self._engine, ctx, instance, end_status="cancelled", actor=actor, reason=reason
            )
            kernel.complete_instance(ctx.session, instance, "cancelled")
            kernel.record_event(
                DomainEvent.PROCESS_CANCELLED,
                {"actor": actor, "reason": reason, "cascaded_instances": cascaded},
                instance_id,
            )

        self._engine.execute(_command)


def _load(ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
    from flowork.persistence.orm import ProcessInstanceRow as _Row

    instance = ctx.session.get(_Row, instance_id)
    if instance is None:
        raise InstanceNotFoundError(f"流程实例不存在：{instance_id}")
    return instance


def _to_info(instance: ProcessInstanceRow) -> InstanceInfo:
    return InstanceInfo(
        id=instance.id,
        definition_code=instance.definition_code,
        definition_major=instance.definition_major,
        definition_revision=instance.definition_revision,
        business_key=instance.business_key,
        state=instance.state,
        start_user=instance.start_user,
        started_at=instance.started_at,
        finished_at=instance.finished_at,
        parent_id=instance.parent_id,
        simulated=instance.simulated,
    )
