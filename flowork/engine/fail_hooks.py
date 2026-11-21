"""on_fail 钩子接线。

前置（错误归因批）已解决：``EngineExecutionError.activity_code`` 由节点作用域
帧标注。本模块在**故障单事务**里据此定位「失败时仍未完结的令牌」，重建
RuntimeNodeContext 后依次执行行为 ``on_fail(ctx, error)`` 与声明式
``hooks.on_fail``（声明式经统一咽喉，错误从 ``ctx.failure`` 取——签名统一
``(ctx, params)``）。

语义口径（对 lifecycle.py 契约图的修订，成文）：

- 触发时机＝故障单落库的独立事务（原命令已整笔回滚）——钩子看到的变量/
  令牌/任务是**回滚后的持久状态**，不是失败瞬间的中间态；
- 令牌状态不变：``TokenState.FAILED`` 仍零写入（失败令牌停留 unfinished、
  实例转 suspended 是现行设计；FAILED 态涉及汇聚/复活语义，属重设计）；
- 无未完令牌但节点在图纸存在 → 以 ``token=None`` 执行（最常见形态：节点在
  失败命令内激活、激活随整笔回滚——钩子观察的是回滚后持久态）；实例本身
  未持久化（start 首节点整笔回滚）→ 不执行；
- 硬不变量延续：on_fail 只可观察与写变量，不得改变路由；
- 钩子自身异常不吞故障单：log 后继续——故障单才是主留痕；
- 模拟实例不执行（模拟绝不执行宿主 handler）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flowork.runtime.lifecycle import TokenState

if TYPE_CHECKING:
    from flowork.engine.context import RuntimeNodeContext
    from flowork.engine.engine import ProcessEngine
    from flowork.engine.ops import CommandContext

__all__ = ["run_fail_hooks"]

logger = logging.getLogger(__name__)


def run_fail_hooks(
    engine: ProcessEngine, ctx: CommandContext, instance_ids: list[str], error: Exception
) -> None:
    """在故障单事务内执行 on_fail（行为钩子 + 声明式钩子）。

    失败定位＝错误归因的 activity_code × 该实例仍 unfinished 的同节点令牌；
    找不到（未持久激活/节点不存在）即静默跳过——不是所有技术故障都有节点
    作用域（Job/消息面错误 activity_code 为 None）。
    """
    from flowork.engine.context import RuntimeNodeContext
    from flowork.engine.declared_hooks import run_declared_hook
    from flowork.persistence.orm import ProcessInstanceRow

    activity = getattr(error, "activity_code", None)
    if not activity:
        return
    kernel = engine.kernel_for(ctx)
    for instance_id in instance_ids:
        instance = ctx.session.get(ProcessInstanceRow, instance_id)
        if instance is None or instance.simulated:
            continue
        node = kernel.definition_of(ctx.session, instance).node(activity)
        if node is None:
            continue
        tokens = [
            row
            for row in kernel.tokens(
                ctx.session, instance_id, states=(TokenState.UNFINISHED.value,)
            )
            if row.activity_code == activity
        ]
        # token=None＝节点在本命令内激活后随整笔回滚（无持久令牌）——仍执行，
        # 钩子以回滚后持久态为现场（典型：service on_enter 即炸）。
        # 多令牌逐个执行（同节点多未完令牌
        # 仅管理员 activate 可构造，修前只对第一个执行）
        for token in tokens if tokens else [None]:
            node_ctx: RuntimeNodeContext = RuntimeNodeContext(
                kernel, ctx.session, instance, node, token
            )
            node_ctx.failure = error
            try:
                kernel.behavior_of(node).on_fail(node_ctx, error)
                run_declared_hook(node_ctx, node.hooks.on_fail)
            except Exception:
                logger.exception(
                    "on_fail 钩子执行失败（instance=%s node=%s）——不吞故障单，故障单照常落库",
                    instance_id,
                    activity,
                )
