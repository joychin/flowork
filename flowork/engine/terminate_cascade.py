"""父实例终态的子树级联（kernel-v2 spec §7，v2.3 第十四轮）。

terminate/cancel 收口为「砍树」：父实例进入终态时，未结算子实例（含暂停/
挂起中的）递归同置终态——孤儿待办是用户可见 bug（A9 多实例子流程后面变大），
对齐 Camunda/Zeebe 子树终止。三入口共用：RuntimeService.terminate_process /
cancel_process、实例时限策略（jobs/strategies cancel/terminate）。

MI 提前收工（subprocess_mi._terminate_running_children）不走本件：那是完成
语义的「提前下班」（paused/suspended 子属管理员域不动，spec §8.3），不是
管理员砍树——树一旦被砍，暂停中的子同样随树终态。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.ops import CommandContext
from flowork.persistence.orm import ProcessInstanceRow
from flowork.runtime.lifecycle import (
    TERMINAL_INSTANCE_STATES,
    InstanceState,
    TokenState,
)

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["cascade_terminate"]

# 结算=终态（与 subprocess_support._CHILD_SETTLED_STATES 同口径）
_SETTLED = TERMINAL_INSTANCE_STATES


def cascade_terminate(
    engine: ProcessEngine,
    ctx: CommandContext,
    root: ProcessInstanceRow,
    *,
    end_status: str,
    actor: str,
    reason: str,
) -> list[str]:
    """撤整棵树的未完令牌，未结算子实例（子先父后）落终态；返回子实例 id 清单。

    顺序契约：**先把树内全部未完令牌撤完，再逐实例 complete**——
    ``resume_parent_if_waiting`` 只在「父令牌仍 unfinished」时推进父侧，
    先撤令牌保证子终态不会误唤醒任何中间层父（与正常完成路径互斥）。
    根实例的 complete 与终态事件由调用方收尾（携带本函数返回的级联清单）。
    """
    from sqlalchemy import select

    from flowork.events.domain import DomainEvent

    kernel = engine.kernel_for(ctx)
    # 1) 收集未结算后代（BFS；环由 seen 剪枝兜底——定义层已拒环，防御性）
    descendants: list[ProcessInstanceRow] = []
    frontier = [root.id]
    seen = {root.id}
    while frontier:
        rows = list(
            ctx.session.scalars(
                select(ProcessInstanceRow).where(
                    ProcessInstanceRow.parent_id.in_(frontier),
                    ProcessInstanceRow.state.not_in(_SETTLED),
                )
            )
        )
        frontier = []
        for row in rows:
            if row.id in seen:
                continue
            seen.add(row.id)
            descendants.append(row)
            frontier.append(row.id)
    # 2) 整树撤令牌（根 + 后代）：cancel_token 经行为取消钩子（wait 关订阅删 Job）
    for instance in [root, *descendants]:
        for token in kernel.tokens(ctx.session, instance.id, states=(TokenState.UNFINISHED.value,)):
            kernel.cancel_token(ctx.session, token)
    # 3) 子实例落终态（逆 BFS＝子先父后）+ 终态事件（cascade_from 溯源）
    terminal_event = (
        DomainEvent.PROCESS_TERMINATED
        if end_status == InstanceState.TERMINATED.value
        else DomainEvent.PROCESS_CANCELLED
    )
    for child in reversed(descendants):
        ctx.touch(child)
        kernel.complete_instance(ctx.session, child, end_status)
        kernel.record_event(
            terminal_event,
            {"actor": actor, "reason": reason, "cascade_from": root.id},
            child.id,
        )
    return [child.id for child in descendants]
