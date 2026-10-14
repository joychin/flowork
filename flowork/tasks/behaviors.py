"""内置节点行为：Start / End / UserTask / ServiceTask / CallActivity / CcTask。
（分支行为在 engine/branch.py 提供；本文件不含任何 if kind 分支——每 kind 一个类。）
"""

from __future__ import annotations

from flowork.definitions.builtin import (
    CallActivitySpec,
    CcTaskSpec,
    EndSpec,
    ServiceTaskSpec,
    StartSpec,
    UserTaskSpec,
    WaitSpec,
)
from flowork.engine.context import RuntimeNodeContext
from flowork.engine.kernel import EngineExecutionError
from flowork.runtime.behavior import NodeBehavior, NodeContext
from flowork.runtime.registry import NodeRegistry
from flowork.tasks.user_task import UserTaskBehavior
from flowork.tasks.wait import WaitBehavior

__all__ = [
    "CallActivityBehavior",
    "CcTaskBehavior",
    "EndBehavior",
    "ServiceTaskBehavior",
    "StartBehavior",
    "UserTaskBehavior",
    "WaitBehavior",
    "register_builtin_behaviors",
]


def register_builtin_behaviors(registry: NodeRegistry) -> None:
    """把内置行为接入注册表（与自定义行为同一入口）。"""
    from flowork.definitions.builtin import BranchSpec
    from flowork.engine.branch import BranchBehavior

    registry.register("start", StartSpec, StartBehavior, replace=True)
    registry.register("end", EndSpec, EndBehavior, replace=True)
    registry.register("user_task", UserTaskSpec, UserTaskBehavior, replace=True)
    registry.register("service_task", ServiceTaskSpec, ServiceTaskBehavior, replace=True)
    registry.register("call_activity", CallActivitySpec, CallActivityBehavior, replace=True)
    registry.register("cc_task", CcTaskSpec, CcTaskBehavior, replace=True)
    registry.register("branch", BranchSpec, BranchBehavior, replace=True)
    registry.register("wait", WaitSpec, WaitBehavior, replace=True)


class StartBehavior(NodeBehavior):
    """开始节点：进入即完成（无任务）。

    例外——驳回到发起（reject_target 指向 start）：为发起人生成
    "修改后重新提交"任务，完成后流程重新从 start 路由（变量可改，分支随之变化）。
    """

    def on_enter(self, ctx: NodeContext) -> None:
        assert isinstance(ctx, RuntimeNodeContext)
        if ctx.token is not None and ctx.token.reject_flag:
            ctx.token.item_count = 1
            ctx.token_updated()
            ctx.create_task(
                assignee=ctx.instance.start_user,
                name="修改后重新提交",
                context_payload={"hint": "申请被驳回，请修改后重新提交"},
            )

    def try_complete(self, ctx: NodeContext) -> bool:
        assert isinstance(ctx, RuntimeNodeContext)
        if ctx.token is not None and ctx.token.reject_flag:
            open_tasks = ctx.kernel.tasks(ctx.session, ctx.instance.id, token_id=ctx.token.id)
            return not open_tasks
        return True


class EndBehavior(NodeBehavior):
    """结束节点：进入即完成实例。"""

    def on_enter(self, ctx: NodeContext) -> None:
        assert isinstance(ctx, RuntimeNodeContext)
        ctx.complete_instance("completed")


class ServiceTaskBehavior(NodeBehavior):
    """系统节点：执行 HandlerRegistry 中注册的动作。"""

    performs_external_work = True  # 模拟试跑恒 stub

    def on_enter(self, ctx: NodeContext) -> None:
        assert isinstance(ctx, RuntimeNodeContext)
        spec = self.spec
        assert isinstance(spec, ServiceTaskSpec)
        if ctx.instance.simulated:  # stub（行业共识：模拟绝不执行外部副作用）
            return
        if not spec.action:
            return
        handler = ctx.kernel.handlers.resolve(spec.action)
        try:
            handler(ctx, spec.params)
        except EngineExecutionError:
            raise
        except Exception as exc:
            # 技术故障→故障单：原始异常包成引擎错误，命令管线挂起实例
            raise EngineExecutionError(
                f"service_task {spec.code} 动作 {spec.action!r} 执行失败：{exc}"
            ) from exc


class CallActivityBehavior(NodeBehavior):
    """子流程节点（完整数据映射在 M5；M1 占位：同步启动子实例并等待）。"""

    def on_enter(self, ctx: NodeContext) -> None:
        from flowork.engine.subprocess_support import start_sub_process

        assert isinstance(ctx, RuntimeNodeContext)
        spec = self.spec
        assert isinstance(spec, CallActivitySpec)
        start_sub_process(ctx, spec)

    def try_complete(self, ctx: NodeContext) -> bool:
        from flowork.engine.subprocess_support import sub_process_finished

        assert isinstance(ctx, RuntimeNodeContext)
        spec = self.spec
        assert isinstance(spec, CallActivitySpec)
        return sub_process_finished(ctx, spec)


class CcTaskBehavior(NodeBehavior):
    """传阅节点：进入即生成传阅件并完成（另提供已读接口）。"""

    def on_enter(self, ctx: NodeContext) -> None:
        from flowork.engine.cc_support import create_cc_items

        assert isinstance(ctx, RuntimeNodeContext)
        spec = self.spec
        assert isinstance(spec, CcTaskSpec)
        if not ctx.instance.simulated:  # 模拟不建传阅件
            create_cc_items(ctx, spec)
