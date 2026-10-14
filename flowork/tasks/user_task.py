"""人工审批节点行为：取人/候选/会签出口/串行换棒。

注册仍由 behaviors.register_builtin_behaviors 统一。
"""

from __future__ import annotations

from typing import Any

from flowork.definitions.builtin import UserTaskSpec
from flowork.engine.context import RuntimeNodeContext
from flowork.engine.kernel import NoParticipantError
from flowork.events.domain import DomainEvent
from flowork.expr.context import expression_functions_of
from flowork.runtime.behavior import NodeBehavior, NodeContext
from flowork.tasks.countersign import exit_reached
from flowork.tasks.deadlines import carry_deadline_jobs_of, schedule_deadline_jobs

__all__ = ["UserTaskBehavior"]


class UserTaskBehavior(NodeBehavior):
    """人工审批节点：进入时解析参与者并生成待办；完成判定走会签计数。"""

    owns_pending_tasks = True  # 名下有待办域（D4 出口不可达兜卫适用）

    def on_enter(self, ctx: NodeContext) -> None:
        assert isinstance(ctx, RuntimeNodeContext)
        spec = self.spec
        assert isinstance(spec, UserTaskSpec)
        assert ctx.token is not None
        if ctx.instance.simulated:  # 模拟不建任务（try_complete 模拟分支放行）
            return
        from flowork.participants.resolver import ParticipantResolution

        resolution: ParticipantResolution = ctx.kernel.resolve_participants(ctx, spec)
        if resolution.candidates:
            self._create_claimable_task(ctx, resolution.candidates)
            return
        if not resolution.todo and not resolution.skipped:
            raise NoParticipantError(
                f"审批节点 {spec.code}（{spec.name or spec.code}）没有可用参与者"
            )
        all_participants = resolution.todo + resolution.skipped
        ctx.token.item_count = len(all_participants)
        ctx.token.approve_count = len(resolution.skipped)  # 自动通过计入同意
        ctx.token_updated()
        for user in resolution.skipped:
            # 幻影抑制：策略命中者建了即归档，task_created
            # 不发（待办通知不误报），统一走 auto_pass 咽喉发 task_auto_passed
            from flowork.tasks.auto_pass import archive_auto_pass

            task = ctx.create_task(assignee=user, emit_event=False)
            archive_auto_pass(
                ctx.kernel,
                ctx.session,
                task,
                comment="参与者策略命中，自动通过",
                reason="strategy",
            )
        sequential = spec.multi_instance is not None and spec.multi_instance.mode == "sequential"
        targets = [resolution.todo[0]] if sequential and resolution.todo else resolution.todo
        created = [ctx.create_task(assignee=assignee) for assignee in targets]
        for task in created:
            schedule_deadline_jobs(ctx, spec, task.id)
        # 全员自动通过时立即完成节点
        if not resolution.todo:
            self._finish_now(ctx)

    def _create_claimable_task(self, ctx: RuntimeNodeContext, candidates: list[str]) -> None:
        """有候选（已求值）：生成可认领任务（assignee 置空 + 候选关联）。

        候选关联＝求值后的人 ∪ 组引用，**引用保留不展开**（候选制下
        组引用保留、认领时再展开——明天入组的人能认领明天的待办）；命中判定见
        `tasks.candidates.candidate_hit`。求值在 ``resolve_participants``
        （候选槽与取人位同一 CEL 求值面）。
        """
        import uuid

        from flowork.persistence.orm import IdentityLinkRow

        assert ctx.token is not None
        spec = self.spec
        assert isinstance(spec, UserTaskSpec)
        ctx.token.item_count = 1
        ctx.token_updated()
        task = ctx.create_task(assignee="")
        # 候选任务同样进 SLA：fire 时未认领由
        # system:timeout 代认领后执行策略（strategies 侧），不再是静默 skip
        schedule_deadline_jobs(ctx, spec, task.id)
        for candidate in candidates:
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
        from flowork.tasks.auto_pass import archive_auto_pass

        remaining = ctx.kernel.tasks(ctx.session, ctx.instance.id, token_id=ctx.token.id)
        for task in remaining:
            archive_auto_pass(
                ctx.kernel,
                ctx.session,
                task,
                comment="会签出口已达成，自动通过",
                reason="exit_reached",
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
            next_task = ctx.create_task(assignee=remaining[0])
            # 换棒 deadline 羁绊：档位属于节点等待
            # 而非任务行——与转办 carry 先例同源（deadlines.py 模块原则）。
            # 修复前旧 Job 绑定已归档任务、到期按 skip 结案，新棒零时限
            # （auto_reject@30 第 1 棒先办结则后续全员无限时窗）。
            # 完成侧与驳回复活（reject_flow）共用本钩子，两侧同修。
            # 旧任务行已被 archive_task 删除——按 id 寻 Job（carry_deadline_jobs_of）。
            carry_deadline_jobs_of(ctx.session, task_id, ctx.instance.id, next_task)

    def _finish_now(self, ctx: RuntimeNodeContext) -> None:
        """全员自动通过：节点立即完成（复用引擎完成路径）。"""
        assert ctx.token is not None
        # 声明式 on_exit 补齐：对齐
        # kernel._finish_and_route 次序契约（行为 on_exit → 声明式 → finish_token）
        # ——修前三处手工完成路径静默跳过宿主钩子
        from flowork.engine.declared_hooks import run_declared_hook

        self.on_exit(ctx)
        run_declared_hook(ctx, ctx.node.hooks.on_exit)
        ctx.kernel.finish_token(ctx.session, ctx.token, approval_exit="agree")
        ctx.kernel.record_event(
            DomainEvent.NODE_EXITED,
            {"activity_code": ctx.node.code, "token_id": ctx.token.id},  # B4
            ctx.instance.id,
        )
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
        if ctx.instance.simulated:  # 模拟即完成（不走 auto_pass 票数语义）
            token.approval_exit = "simulated"
            return True
        mi = spec.multi_instance
        from flowork.tasks.facts import instance_facts

        counts: dict[str, Any] = {
            "item_count": token.item_count,
            "approve_count": token.approve_count,
            "disapprove_count": token.disapprove_count,
            "variables": ctx.variables,
            "start_user": ctx.instance.start_user,
            "functions": expression_functions_of(
                ctx.kernel.engine.config if ctx.kernel.engine else None
            ),
            "instance": instance_facts(ctx.instance),
            "now": ctx.kernel.config.now(),
        }
        if exit_reached(mi.approve_exit if mi else None, side="approve", **counts):
            token.approval_exit = "agree"
            return True
        if (
            mi is not None
            and mi.disapprove_exit
            and exit_reached(mi.disapprove_exit, side="disapprove", **counts)
        ):
            token.approval_exit = "reject"
            return True
        return False
