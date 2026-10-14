"""故障修复动作 resume/retry 的挂起恢复共享体。

状态守卫：仅 suspended 可恢复——修前
terminate 不联动关单，对残存 open 单 resolve(resume) 可把已终止实例复活为
running（零未完令牌永久卡死）；paused 实例走本路径会绕过 pause 恢复语义
（timer 顺延/缓冲补消费全跳过）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.errors import FloworkError
from flowork.events.domain import DomainEvent
from flowork.runtime.lifecycle import TERMINAL_INSTANCE_STATES, InstanceState

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.engine.ops import CommandContext
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["resume_from_suspension"]


def resume_from_suspension(
    engine: ProcessEngine,
    ctx: CommandContext,
    instance: ProcessInstanceRow,
    actor: str,
    reason: str,
) -> None:
    """置 running + 补消息 + 补推进 + process_resumed 事件（挂起/恢复对称）。"""
    from flowork.engine.instance_lifecycle import advance_unfinished_tokens
    from flowork.engine.message_flow import retro_poll_instance

    if instance.state != InstanceState.SUSPENDED.value:
        if instance.state in TERMINAL_INSTANCE_STATES:
            raise FloworkError(
                f"实例 {instance.id} 已终态（{instance.state}），故障单不可恢复运行"
                "（实例终止/取消时 open 故障单已联动关闭）"
            )
        raise FloworkError(
            f"实例 {instance.id} 状态 {instance.state!r} 不可恢复（须 suspended；"
            "paused 实例请先 resume_process 再处理故障单）"
        )
    ctx.touch(instance)
    instance.state = InstanceState.RUNNING.value
    # 挂起期消息落缓冲——恢复后补消费（对齐 resume_process）
    retro_poll_instance(ctx, engine, instance)
    # 挂起期间到达的子实例完结补推进：
    # 修前只补消息——父恢复后等待子节点永久卡住
    advance_unfinished_tokens(engine, ctx, instance)
    engine.kernel_for(ctx).record_event(
        DomainEvent.PROCESS_RESUMED,
        {"actor": actor, "reason": reason, "deferred_minutes": None},
        instance.id,
    )
