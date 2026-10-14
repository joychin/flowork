"""声明式钩子执行（spec §2.6 层 1）：节点级 hooks 与连线 on_take 的统一咽喉。

此前 ``FlowNode.hooks`` 被 pydantic 收下但引擎零读取——图纸声明静默失效、
校验零告警（实证：注入 hooks 后 handler 调用 0 次）。
本模块把三个时机（on_enter/on_exit/on_cancel）接进内核生命周期；连线
``flow.on_take`` 原为裸 resolve 直调，一并归一到同一咽喉：

- **模拟试跑跳过**（模拟绝不执行宿主 handler）。on_take 此前
  无守卫，试跑会真执行外部副作用，属隔离漏，已修复；
- **未注册 handler ＝ 技术故障**（EngineExecutionError → 挂起+故障单），与
  部署期 L3 校验（validator_deploy 的 node.hooks/flow.on_take handler_unknown，
  WARNING、strict 升 ERROR）双保险——「先部署后注册」仍是合法模式，运行期
  兜底保证漏网定义失败可见；
- **handler 异常包引擎错误**（对齐 ServiceTaskBehavior 的 spec §8 语义）。

执行次序：on_enter 先于行为钩子（handler 写 ctx.variables 供行为与完成判定
消费）；on_exit 后于行为钩子、先于 finish_token；on_cancel 先于行为钩子。
on_fail 经 fail_hooks.py 在故障单事务内执行——声明式侧错误经 ctx.failure
传递，仍走本咽喉的 resolve/异常包语义。
硬不变量（runtime/behavior.py）：钩子只可观察与写变量，不得改变路由。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.errors import UnknownHandler

if TYPE_CHECKING:
    from flowork.definitions.node import HandlerRef
    from flowork.engine.context import RuntimeNodeContext

__all__ = ["run_declared_hook"]


def run_declared_hook(ctx: RuntimeNodeContext, ref: HandlerRef | None) -> None:
    """执行一处声明式钩子；未声明（None）与模拟实例直接跳过。

    未注册与 handler 自身异常都归一为 EngineExecutionError——命令管线挂起
    实例并落故障单（spec §8 技术故障），宿主不再拿到裸异常零留痕。
    """
    from flowork.engine.kernel import EngineExecutionError

    if ref is None or ctx.instance.simulated:
        return
    try:
        handler = ctx.kernel.handlers.resolve(ref.handler)
    except UnknownHandler as exc:
        raise EngineExecutionError(
            f"节点 {ctx.node.code} 声明式钩子引用未注册的 handler {ref.handler!r}"
            "（部署期校验 node.hooks/flow.on_take.handler_unknown 可提前发现）"
        ) from exc
    try:
        handler(ctx, ref.params)
    except EngineExecutionError:
        raise
    except Exception as exc:
        raise EngineExecutionError(
            f"节点 {ctx.node.code} 声明式钩子 {ref.handler!r} 执行失败：{exc}"
        ) from exc
