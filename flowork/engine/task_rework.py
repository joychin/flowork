"""加签与撤回面（TaskService 组合件，入口 engine.task.* 不变）。

从 task_actions.py 拆出（单文件 ≤300 行回归）：
add_countersigner / withdraw。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.ops import CommandContext
from flowork.errors import TaskNotAvailableError
from flowork.persistence.orm import TaskHistoryRow, TokenRow
from flowork.runtime.lifecycle import TokenState
from flowork.tasks.models import TaskInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow, TaskRow

__all__ = ["TaskReworkMixin"]


class TaskReworkMixin:
    """加签/撤回；宿主 TaskService 提供 _engine 与共享助手（_ready_task/_clone_task 等）。"""

    _engine: ProcessEngine

    def _instance(self, ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
        """实例加载由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _ready_task(
        self,
        ctx: CommandContext,
        task_id: str,
        expected_type: str | None = None,
        *,
        action: str | None = None,
    ) -> TaskRow:
        """待办加载与守卫（含动作表判定）由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _clone_task(
        self,
        ctx: CommandContext,
        task: TaskRow,
        *,
        assignee: str,
        task_type: str | None = None,
        root: str | None = None,
    ) -> TaskRow:
        """任务克隆由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def _task_info(self, task: TaskRow) -> TaskInfo:
        """任务视图由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def add_countersigner(
        self, task_id: str, users: list[str], actor: str, comment: str = ""
    ) -> list[TaskInfo]:
        """加签：向当前令牌追加会签人（item_count 同步增加，新待办立即生成）。

        前加签/后加签在并签池中语义等价（均需完成）；串行场景按剩余顺序排队。
        """
        result: list[TaskInfo] = self._engine.execute(
            lambda ctx: self._add_countersigner(ctx, task_id, users, actor, comment)
        )
        return result

    def _add_countersigner(
        self, ctx: CommandContext, task_id: str, users: list[str], actor: str, comment: str
    ) -> list[TaskInfo]:
        from flowork.definitions.builtin import UserTaskSpec

        task = self._ready_task(ctx, task_id, action="add_countersigner")
        token = ctx.session.get(TokenRow, task.token_id)
        assert token is not None
        # 名单口径对齐：节点原名单 ∪ 已有手动名单 ∪ 新人 → item_count 与可办名单一致
        kernel = self._engine.kernel_for(ctx)
        instance = self._instance(ctx, task.instance_id)
        ctx.touch(instance)
        node = kernel.definition_of(ctx.session, instance).node(task.activity_code)
        base_participants: list[str] = []
        if isinstance(node, UserTaskSpec):
            # 名单口径对齐：节点表达式先解析为具体人（manual_participants 存人，不存表达式原文）
            from flowork.expr.context import expression_functions_of
            from flowork.participants.resolver import resolve_subjects
            from flowork.tasks.facts import attach_node, instance_facts, with_prev_task

            expressions = ([node.assignee] if node.assignee else []) + list(node.assignees)
            base_participants = resolve_subjects(
                expressions,
                with_prev_task(ctx.session, instance.id, instance.variables or {}),
                instance.start_user,
                attach_node(expression_functions_of(self._engine.config), ctx.session, instance.id),
                label=task.activity_code,
                instance=instance_facts(instance),
                now=self._engine.config.now(),
            )
        existing = list((token.manual_participants or {}).get(task.activity_code, []))
        merged = list(dict.fromkeys([*base_participants, *existing, *users]))
        # 规模护栏（spec v2.3）：加签是调用方请求——超限拒绝整笔（回滚），实例不挂起；
        # limit 缺省 None＝不设防（第十八轮：内核不替宿主选数值）
        limit = self._engine.config.max_participants
        if limit is not None and len(merged) > limit:
            raise TaskNotAvailableError(
                f"节点 {task.activity_code} 加签后名单 {len(merged)} 人超过上限 {limit}"
                "（EngineConfig.max_participants）——拒绝而非截断"
            )
        token.manual_participants[task.activity_code] = merged
        token.item_count = len(merged)
        ctx.session.flush()
        kernel = self._engine.kernel_for(ctx)
        kernel.record_event(
            "task_countersigner_added",
            {"task_id": task_id, "users": users, "actor": actor, "comment": comment},
            task.instance_id,
        )
        root = task.root_task_id or task.id
        created = [self._clone_task(ctx, task, assignee=user, root=root) for user in users]
        return [self._task_info(t) for t in created]

    def withdraw(self, task_id: str, actor: str, comment: str = "") -> TaskInfo:
        """撤回本人已办任务：取消其后已激活的下游令牌，并在原节点重建待办。"""
        result: TaskInfo = self._engine.execute(
            lambda ctx: self._withdraw(ctx, task_id, actor, comment)
        )
        return result

    def _withdraw(self, ctx: CommandContext, task_id: str, actor: str, comment: str) -> TaskInfo:
        from sqlalchemy import select

        history = ctx.session.scalars(
            select(TaskHistoryRow).where(TaskHistoryRow.task_id == task_id)
        ).first()
        if history is None or history.outcome != "agree" or history.acted_by != actor:
            raise TaskNotAvailableError(f"任务不可撤回（未办理/非本人/非同意）：{task_id}")
        instance = self._instance(ctx, history.instance_id)
        if instance.state != "running":
            raise TaskNotAvailableError("实例已结束，不可撤回")
        ctx.touch(instance)
        kernel = self._engine.kernel_for(ctx)
        self._assert_downstream_idle(ctx, instance, history.activity_code)
        kernel = self._engine.kernel_for(ctx)
        # 只取消下游盆内的未完令牌（code-review 2026-10 P0-3）：此前的「取消全部
        # 未完」在含并行分叉的图上会把仍在办理的兄弟分支一并砍掉——兄弟令牌不在
        # 本节点下游、闸门不拦，其审批人待办被静默作废；拍板④「取消全部与只取消
        # 下游等价」只在无并行分叉时成立，不能当不变量。串行图全部未完令牌本就
        # 在盆内，行为不变。
        from flowork.definitions.graph import downstream_basin

        definition = kernel.definition_of(ctx.session, instance)
        downstream = downstream_basin(definition, history.activity_code) - {history.activity_code}
        for token in kernel.tokens(ctx.session, instance.id, states=(TokenState.UNFINISHED.value,)):
            if token.activity_code in downstream:
                kernel.cancel_token(ctx.session, token)
        # 原节点重建：新令牌 + 原办理人待办
        node = definition.node(history.activity_code)
        assert node is not None
        token = kernel.activate(
            ctx.session,
            instance,
            node,
            source_activity_code=None,
            manual_participants={node.code: [history.assignee]},
        )
        kernel.record_event(
            "task_withdrawn",
            {"task_id": task_id, "actor": actor, "comment": comment, "new_token": token.id},
            instance.id,
        )
        tasks = kernel.tasks(ctx.session, instance.id, token_id=token.id)
        return self._task_info(tasks[0])

    def _assert_downstream_idle(
        self, ctx: CommandContext, instance: ProcessInstanceRow, activity_code: str
    ) -> None:
        """撤回下游闸门（v2.3 第二批 A2，spec §5.1 拍板④）。

        本节点正向可达的下游存在**人工进展** → 拒绝撤回（提示改用驳回）——
        否则撤回等于一次没有驳回留痕的隐式整体驳回。进展判据两条：
        ① 下游存在表决行（表决行口径，spec §5.1 第十九轮——协办/咨询/自动
        通过不算进展；branch/cc/service 不产生任务历史天然只统计人工办理）；
        ② 下游**工作节点**（user_task/call_activity）存在已完结令牌——穿透节点
        （branch/cc_task/start/end）的即时完结**不拦**，对齐 对标产品
        getChildRevokeActivityCodes 穿透 CONNECTION/CIRCULATE 的口径（pilot
        leave 实证：branch 即刻完结曾误拦）；判据②同时覆盖「节点整体完成
        （含出口自动通过）」的进展。判据保守方向：不评条件，未命中分支上的
        人工进展同样拦截。行业零实现，flowork 首创（delta-industry-benchmark §2）。
        """
        from sqlalchemy import select

        from flowork.definitions.graph import downstream_basin
        from flowork.tasks.vote import vote_criteria

        kernel = self._engine.kernel_for(ctx)
        definition = kernel.definition_of(ctx.session, instance)
        downstream = downstream_basin(definition, activity_code) - {activity_code}
        if not downstream:
            return
        from flowork.persistence.orm import TaskHistoryRow as _History
        from flowork.runtime.lifecycle import TokenState as _State

        progressed: list[str] = []
        codes = tuple(downstream)
        for row in ctx.session.scalars(
            select(_History).where(
                _History.instance_id == instance.id,
                _History.activity_code.in_(codes),
                *vote_criteria(),
            )
        ):
            progressed.append(f"节点 {row.activity_code} 由 {row.assignee} 办理（{row.outcome}）")
        if not progressed:
            work_kinds = {"user_task", "call_activity"}
            for token in kernel.tokens(ctx.session, instance.id):
                if token.state != _State.FINISHED.value or token.activity_code not in codes:
                    continue
                node = definition.node(token.activity_code)
                if node is not None and node.kind in work_kinds:
                    progressed.append(f"节点 {token.activity_code} 已完结（令牌）")
        if progressed:
            raise TaskNotAvailableError(
                "下游已办理，不可撤回；请使用驳回（已产生进展：" + "；".join(progressed[:3]) + "）"
            )
