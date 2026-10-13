"""撤回面（TaskService 组合件，入口 engine.task.withdraw 不变）。

加签拆至 task_countersign_add.py、协办拆至 task_assist.py（本文件曾三合一
超红线）；本文件保留撤回（含拍板 3 同令牌撤票重建）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.ops import CommandContext
from flowork.errors import TaskNotAvailableError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import TaskHistoryRow, TokenRow
from flowork.runtime.lifecycle import InstanceState, TokenState
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
        if instance.state != InstanceState.RUNNING.value:
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
                kernel.cancel_token(ctx.session, token, cause="withdraw")

        node = definition.node(history.activity_code)
        assert node is not None
        # 撤回分流（拍板 3 方案 A 实证深化）：原令牌仍在该节点未完结（并行会签
        # 出口未达成）→ 撤票回退计数 + 同令牌重建待办——实证修复前该场景建第二
        # 令牌：撤回的票仍算数（原令牌 approve 不回退）、双令牌双推进（下游出现
        # 重复待办）。原令牌已完结（单任务节点）→ 既有路径：新令牌重建。
        original_token = ctx.session.get(TokenRow, history.token_id)
        if (
            original_token is not None
            and original_token.state == TokenState.UNFINISHED.value
            and original_token.activity_code == history.activity_code
        ):
            from flowork.definitions.builtin import UserTaskSpec

            spec = node if isinstance(node, UserTaskSpec) else None
            assert spec is not None
            if spec.multi_instance is not None and spec.multi_instance.mode == "sequential":
                # 串行换棒的轮次语义与撤回纠缠（轮次已消耗/下棒在办）——不支持，
                # 指路驳回（驳回有完整的复活圈定语义）
                raise TaskNotAvailableError(
                    "串行会签节点的已办暂不支持撤回（轮次已交棒）：请使用驳回"
                )
            original_token.approve_count = max(0, original_token.approve_count - 1)
            kernel.create_task(
                ctx.session,
                instance,
                original_token,
                node,
                assignee=history.assignee,
                name=history.name,
            )
            ctx.session.flush()
            kernel.record_event(
                DomainEvent.TASK_WITHDRAWN,
                {
                    "task_id": task_id,
                    "activity_code": history.activity_code,
                    "actor": actor,
                    "comment": comment,
                    "token_id": original_token.id,
                    "vote_retracted": True,
                },
                instance.id,
            )
            tasks = kernel.tasks(ctx.session, instance.id, token_id=original_token.id)
            return self._task_info(tasks[-1])
        # 原节点重建：新令牌 + 原办理人待办
        token = kernel.activate(
            ctx.session,
            instance,
            node,
            source_activity_code=None,
            manual_participants={node.code: [history.assignee]},
        )
        kernel.record_event(
            DomainEvent.TASK_WITHDRAWN,
            {
                "task_id": task_id,
                "activity_code": history.activity_code,
                "actor": actor,
                "comment": comment,
                "new_token": token.id,
            },
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
        （branch/cc_task/start/end）的即时完结**不拦**，对齐穿透型节点不拦的行业口径（pilot
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
