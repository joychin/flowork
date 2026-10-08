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
)
from flowork.engine.context import RuntimeNodeContext
from flowork.engine.kernel import EngineExecutionError, NoParticipantError
from flowork.runtime.behavior import NodeBehavior, NodeContext
from flowork.runtime.registry import NodeRegistry
from flowork.tasks.countersign import evaluate_exit

__all__ = [
    "CallActivityBehavior",
    "CcTaskBehavior",
    "EndBehavior",
    "ServiceTaskBehavior",
    "StartBehavior",
    "UserTaskBehavior",
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


class UserTaskBehavior(NodeBehavior):
    """人工审批节点：进入时解析参与者并生成待办；完成判定走会签计数。"""

    def on_enter(self, ctx: NodeContext) -> None:
        assert isinstance(ctx, RuntimeNodeContext)
        spec = self.spec
        assert isinstance(spec, UserTaskSpec)
        assert ctx.token is not None
        from flowork.participants.resolver import ParticipantResolution

        resolution: ParticipantResolution = ctx.kernel.resolve_participants(ctx, spec)
        if not resolution.todo and not resolution.skipped:
            if spec.candidate_users or spec.candidate_groups:
                self._create_claimable_task(ctx, spec)
                return
            raise NoParticipantError(
                f"审批节点 {spec.code}（{spec.name or spec.code}）没有可用参与者"
            )
        all_participants = resolution.todo + resolution.skipped
        ctx.token.item_count = len(all_participants)
        ctx.token.approve_count = len(resolution.skipped)  # 自动通过计入同意
        ctx.token_updated()
        for user in resolution.skipped:
            task = ctx.create_task(assignee=user)
            ctx.kernel.archive_task(
                ctx.session,
                task,
                outcome="auto_pass",
                comment="参与者策略命中，自动通过",
                actor="system",
            )
        sequential = spec.multi_instance is not None and spec.multi_instance.mode == "sequential"
        targets = [resolution.todo[0]] if sequential and resolution.todo else resolution.todo
        created = [ctx.create_task(assignee=assignee) for assignee in targets]
        for task in created:
            _schedule_timeout(ctx, spec, task.id)
        # 全员自动通过时立即完成节点
        if not resolution.todo:
            self._finish_now(ctx)

    def _create_claimable_task(self, ctx: RuntimeNodeContext, spec: UserTaskSpec) -> None:
        """无指定办理人但有候选人：生成可认领任务（assignee 置空 + 候选关联）。"""
        import uuid

        from flowork.persistence.orm import IdentityLinkRow

        assert ctx.token is not None
        ctx.token.item_count = 1
        ctx.token_updated()
        task = ctx.create_task(assignee="")
        _schedule_timeout(ctx, spec, task.id)  # 超时策略同样适用于认领场景
        candidates = list(spec.candidate_users) + list(spec.candidate_groups)
        for candidate in dict.fromkeys(candidates):
            ctx.session.add(
                IdentityLinkRow(
                    id=uuid.uuid4().hex,
                    task_id=task.id,
                    user_id=candidate,
                    link_type="candidate",
                )
            )
        ctx.session.flush()

    def on_exit(self, ctx: NodeContext) -> None:
        """会签出口达成：本令牌剩余待办撤销（记录为 auto_pass）。"""
        assert isinstance(ctx, RuntimeNodeContext)
        assert ctx.token is not None
        remaining = ctx.kernel.tasks(ctx.session, ctx.instance.id, token_id=ctx.token.id)
        for task in remaining:
            ctx.kernel.archive_task(
                ctx.session,
                task,
                outcome="auto_pass",
                comment="会签出口已达成，自动通过",
                actor="system",
            )

    def on_task_completed(self, ctx: NodeContext, task_id: str, outcome: str) -> None:
        """串行会签：当前出口未达成时为下一位参与者生成待办。"""
        assert isinstance(ctx, RuntimeNodeContext)
        spec = self.spec
        assert isinstance(spec, UserTaskSpec)
        if spec.multi_instance is None or spec.multi_instance.mode != "sequential":
            return
        assert ctx.token is not None
        from flowork.participants.resolver import ParticipantResolution

        resolution: ParticipantResolution = ctx.kernel.resolve_participants(ctx, spec)
        participants = resolution.todo + resolution.skipped
        already = set(ctx.kernel.handled_assignees(ctx.session, ctx.token.id))
        remaining = [p for p in participants if p not in already]
        if remaining and not self._exit_reached(ctx):
            ctx.create_task(assignee=remaining[0])

    def _finish_now(self, ctx: RuntimeNodeContext) -> None:
        """全员自动通过：节点立即完成（复用引擎完成路径）。"""
        assert ctx.token is not None
        ctx.kernel.finish_token(ctx.session, ctx.token, approval_exit="agree")
        ctx.kernel.record_event("node_exited", {"activity_code": ctx.node.code}, ctx.instance.id)
        node = ctx.kernel.definition_of(ctx.session, ctx.instance).node(ctx.node.code)
        assert node is not None
        ctx.kernel.route(ctx.session, ctx.instance, node, ctx.token)

    def _exit_reached(self, ctx: RuntimeNodeContext) -> bool:
        assert ctx.token is not None
        return self.try_complete(ctx)

    def try_complete(self, ctx: NodeContext) -> bool:
        assert isinstance(ctx, RuntimeNodeContext)
        spec = self.spec
        assert isinstance(spec, UserTaskSpec)
        token = ctx.token
        assert token is not None
        mi = spec.multi_instance
        if evaluate_exit(mi.approve_exit if mi else None, token.item_count, token.approve_count):
            token.approval_exit = "agree"
            return True
        if (
            mi is not None
            and mi.disapprove_exit
            and evaluate_exit(mi.disapprove_exit, token.item_count, token.disapprove_count)
        ):
            token.approval_exit = "reject"
            return True
        return False


class ServiceTaskBehavior(NodeBehavior):
    """系统节点：执行 HandlerRegistry 中注册的动作。"""

    def on_enter(self, ctx: NodeContext) -> None:
        assert isinstance(ctx, RuntimeNodeContext)
        spec = self.spec
        assert isinstance(spec, ServiceTaskSpec)
        if not spec.action:
            return
        handler = ctx.kernel.handlers.resolve(spec.action)
        try:
            handler(ctx, spec.params)
        except EngineExecutionError:
            raise
        except Exception as exc:
            # 技术故障→故障单（spec §8）：原始异常包成引擎错误，命令管线挂起实例
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
    """传阅节点：进入即生成传阅件并完成（M3 提供已读接口）。"""

    def on_enter(self, ctx: NodeContext) -> None:
        from flowork.engine.cc_support import create_cc_items

        assert isinstance(ctx, RuntimeNodeContext)
        spec = self.spec
        assert isinstance(spec, CcTaskSpec)
        create_cc_items(ctx, spec)


def _schedule_timeout(ctx: RuntimeNodeContext, spec: UserTaskSpec, task_id: str) -> None:
    """按节点超时配置创建 timer Job（v2.2：deadlines 列表逐条建 Job，≤8 条）。

    首条到期时刻物化进 task.due_at（due_before 过滤与排序，spec §5.1）；
    auto_approve/auto_reject 终结等待后剩余 Job 因任务已完自然跳过。
    """
    from flowork.definitions.node import effective_deadlines

    deadlines = effective_deadlines(spec)
    if not deadlines:
        return
    import uuid
    from datetime import timedelta

    from flowork.persistence.orm import JobRow, TaskRow

    now = ctx.kernel.config.now()
    for index, deadline in enumerate(deadlines):
        ctx.session.add(
            JobRow(
                id=uuid.uuid4().hex,
                job_type="timer",
                state="scheduled",
                run_at=now + timedelta(minutes=deadline.after_minutes),
                instance_id=ctx.instance.id,
                payload={
                    "task_id": task_id,
                    "activity_code": spec.code,
                    "kind": "task_deadline",
                    "strategy": deadline.action.value,
                    "after_minutes": deadline.after_minutes,
                    "deadline_index": index,
                    **({"reassign_to": deadline.reassign_to} if deadline.reassign_to else {}),
                },
            )
        )
    task = ctx.session.get(TaskRow, task_id)
    if task is not None:
        task.due_at = now + timedelta(minutes=deadlines[0].after_minutes)
