"""实例生命周期与故障处置：pause/resume/terminate/cancel、
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
from flowork.runtime.lifecycle import TERMINAL_INSTANCE_STATES, InstanceState
from flowork.tasks.models import InstanceInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["InstanceLifecycleMixin", "advance_unfinished_tokens"]


def advance_unfinished_tokens(
    engine: ProcessEngine, ctx: CommandContext, instance: ProcessInstanceRow
) -> None:
    """恢复（暂停/挂起）后数据驱动补一轮推进。

    冻结期间到达的子实例完结不会唤醒父（resume_parent_if_waiting 在父非
    running 时直接 return、不留补偿）——恢复后逐未完令牌问行为 try_complete
    （kind 无关：等待中的 CallActivity 子实例已全部结算即放行；user_task/
    wait 等行为自判 no-op）。暂停恢复与故障恢复
    （挂起期间子完成，父 resume 后永久卡住）
    共用本补偿。
    """
    from flowork.runtime.lifecycle import TokenState

    kernel = engine.kernel_for(ctx)
    for token in kernel.tokens(ctx.session, instance.id, states=(TokenState.UNFINISHED.value,)):
        kernel.advance_after_task(ctx.session, instance, token)


class InstanceLifecycleMixin:
    """RuntimeService 的生命周期/故障处置面。"""

    _engine: ProcessEngine

    def pause_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """暂停实例：任务不可办、timer 冻结；恢复后按自然时长顺延。"""

        def _command(ctx: CommandContext) -> Any:
            instance = _load(ctx, instance_id)
            if instance.state != InstanceState.RUNNING.value:
                raise InstanceNotFoundError(
                    f"实例 {instance_id} 状态 {instance.state!r} 不可暂停（须 running）"
                )
            ctx.touch(instance)
            instance.state = InstanceState.PAUSED.value
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
            if instance.state != InstanceState.PAUSED.value:
                raise InstanceNotFoundError(
                    f"实例 {instance_id} 状态 {instance.state!r} 不可恢复（须 paused）"
                )
            ctx.touch(instance)
            now = self._engine.config.now()
            paused_minutes = 0
            if instance.paused_at is not None:
                # 向上取整（2026-10-21 内核 review P2）：修前 //60 向下取整，
                # 90 秒暂停只挪 60 秒——定时器提前烧
                import math

                paused_minutes = math.ceil((now - instance.paused_at).total_seconds() / 60)
            instance.state = InstanceState.RUNNING.value
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
            # 暂停期间到达的消息（落缓冲）反查：open 订阅补消费。
            from flowork.engine.message_flow import retro_poll_instance

            retro_poll_instance(ctx, self._engine, instance)

            # 冻结期间到达的子实例完结补推进，
            # 与故障恢复共用补偿（见 advance_unfinished_tokens）。
            advance_unfinished_tokens(self._engine, ctx, instance)

        self._engine.execute(_command)

    def terminate_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """终止实例：管理员强杀——整树撤令牌与待办，终态 terminated。

        级联未结算子实例（含暂停/挂起中的）同置 terminated
        （terminate_cascade——孤儿待办收口，对齐 Camunda/Zeebe 子树终止）。
        """
        self._engine.execute(
            lambda ctx: self._terminate_in_ctx(ctx, instance_id, actor=actor, reason=reason)
        )

    def _terminate_in_ctx(
        self,
        ctx: CommandContext,
        instance_id: str,
        *,
        actor: str,
        reason: str,
        close_incidents: bool = True,
    ) -> None:
        """终止的命令体内联体：供 resolve_incident
        在同一事务内调用——嵌套 engine.execute 曾把 incident 已 resolved 与实例
        终态拆进两笔事务，中间崩溃＝实例还跑、单子永闭。"""
        instance = _load(ctx, instance_id)
        if instance.state in TERMINAL_INSTANCE_STATES:
            raise InstanceNotFoundError(f"实例 {instance_id} 已终态（{instance.state}）")
        ctx.touch(instance)
        kernel = self._engine.kernel_for(ctx)
        from flowork.engine.terminate_cascade import cascade_terminate

        cascaded = cascade_terminate(
            self._engine,
            ctx,
            instance,
            end_status="terminated",
            actor=actor,
            reason=reason,
            close_incidents=close_incidents,
        )
        kernel.complete_instance(ctx.session, instance, "terminated")
        kernel.record_event(
            DomainEvent.PROCESS_TERMINATED,
            {"actor": actor, "reason": reason, "cascaded_instances": cascaded},
            instance_id,
        )

    def cancel_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """取消实例：整树撤销未完令牌与待办（子实例级联 cancelled）。"""
        self._engine.execute(
            lambda ctx: self._cancel_in_ctx(ctx, instance_id, actor=actor, reason=reason)
        )

    def _cancel_in_ctx(
        self,
        ctx: CommandContext,
        instance_id: str,
        *,
        actor: str,
        reason: str,
        close_incidents: bool = True,
    ) -> None:
        """取消的命令体内联体（同 _terminate_in_ctx）。"""
        instance = _load(ctx, instance_id)
        if instance.state in TERMINAL_INSTANCE_STATES:
            # 终态守卫：对齐 terminate_process——
            # complete_instance 对终态静默跳过，但本命令的取消事件无条件发，
            # 曾对 completed 实例发出假 process_cancelled（状态与事件背离）
            raise InstanceNotFoundError(f"实例 {instance_id} 已终态（{instance.state}），不可取消")
        ctx.touch(instance)
        kernel = self._engine.kernel_for(ctx)
        from flowork.engine.terminate_cascade import cascade_terminate

        cascaded = cascade_terminate(
            self._engine,
            ctx,
            instance,
            end_status="cancelled",
            actor=actor,
            reason=reason,
            close_incidents=close_incidents,
        )
        kernel.complete_instance(ctx.session, instance, "cancelled")
        kernel.record_event(
            DomainEvent.PROCESS_CANCELLED,
            {"actor": actor, "reason": reason, "cascaded_instances": cascaded},
            instance_id,
        )


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
