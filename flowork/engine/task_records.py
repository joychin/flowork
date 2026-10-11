"""任务记录与参与者解析面：建任务所需的一切（Kernel 组合件，入口类不变）。

从 kernel.py 拆出（单文件 ≤300 行回归）；Kernel 经 Mixin 组合获得本面。
``record_event`` 由 Kernel 提供，本类只留类型桩；
EngineExecutionError 住 kernel.py，函数内延迟导入避免模块环。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.definitions.builtin import UserTaskSpec
from flowork.engine.token_records import _new_id
from flowork.events.domain import DomainEvent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.node import FlowNode
    from flowork.engine.config import EngineConfig
    from flowork.engine.context import RuntimeNodeContext
    from flowork.persistence.orm import ProcessInstanceRow, TaskRow, TokenRow

__all__ = ["TaskRecordsMixin"]


class TaskRecordsMixin:
    """任务 CRUD、身份快照、归档与参与者解析；宿主 Kernel 提供 config/engine/record_event。"""

    config: EngineConfig
    engine: Any

    MAX_ACTOR_CONTEXT_BYTES = 4096  # spec §5.6：JSON 袋上限，超限拒收

    def record_event(self, name: str, payload: dict[str, Any], instance_id: str | None) -> None:
        """事件记录由 Kernel 提供。"""
        raise NotImplementedError

    def create_task(
        self,
        session: Session,
        instance: ProcessInstanceRow,
        token: TokenRow,
        node: FlowNode,
        *,
        assignee: str,
        name: str,
        task_type: str = "approval",
        context_payload: dict[str, Any] | None = None,
        root_task_id: str | None = None,
    ) -> TaskRow:
        from flowork.persistence.orm import TaskRow as _TaskRow

        task = _TaskRow(
            id=_new_id(),
            # 业务时刻与 due_at/cost_seconds 同源 config.now（P1-10；列 default 仅兜底）
            created_at=self.config.now(),
            instance_id=instance.id,
            token_id=token.id,
            activity_code=node.code,
            name=name,
            assignee=assignee,
            task_type=task_type,
            context_payload=context_payload or {},
            root_task_id=root_task_id,
        )
        session.add(task)
        session.flush()
        self.record_event(
            DomainEvent.TASK_CREATED,
            {
                "task_id": task.id,
                "activity_code": node.code,
                "assignee": assignee,
                "task_type": task_type,
            },
            instance.id,
        )
        return task

    def tasks(
        self,
        session: Session,
        instance_id: str,
        *,
        token_id: str | None = None,
        assignee: str | None = None,
    ) -> list[TaskRow]:
        from sqlalchemy import select

        from flowork.persistence.orm import TaskRow as _TaskRow

        stmt = select(_TaskRow).where(_TaskRow.instance_id == instance_id)
        if token_id:
            stmt = stmt.where(_TaskRow.token_id == token_id)
        if assignee:
            stmt = stmt.where(_TaskRow.assignee == assignee)
        return list(session.scalars(stmt))

    def resolve_participants(self, ctx: RuntimeNodeContext, spec: UserTaskSpec) -> Any:
        """参与者解析入口：返回 ParticipantResolution（todo/skipped/candidates）。

        单轨 CEL（spec §4.2）：函数表自 EngineConfig.expression_functions 拍平；
        有组织目录叠加委托替换与 to_admins 兜底，无目录走同一条 CEL 求值路径。
        出口统一守规模护栏（max_participants，spec v2.3）：手动名单与表达式名单同守。

        三条判定（spec §5.5，2026-10-09 B 批「候选槽表达式对齐」）：

        - **指派优先于候选**：取人位解析出人 → 直派（候选槽不参与）；
        - **声明了候选槽就不走兜底**——候选制下「解析不出具体办理人」是正常态，
          改走候选槽**求值**（`resolve_candidate_subjects`，与取人位同一求值面）；
        - **候选求值也空**才回兜底（to_admins / raise）——与未声明候选同一条路。

        第四条（第十八轮）：**单人节点多人＝错误**——未声明 multi_instance 而
        解析出多名 todo，拒绝宽松并行（`ParticipantCountMismatchError`），
        多人必须显式声明会签。
        """
        from flowork.expr.context import expression_functions_of
        from flowork.participants.resolver import (
            ParticipantResolution,
            assert_participant_limit,
            fallback_when_empty,
            resolve_candidate_subjects,
            resolve_participants,
            resolve_with_delegation,
        )

        config = self.engine.config if self.engine else None
        functions = expression_functions_of(config)
        org = config.org if config is not None else None
        limit = config.max_participants if config is not None else None
        where = f"审批节点 {spec.code}"

        if org is not None:
            resolution, _delegation = resolve_with_delegation(ctx, spec, org, functions)
        else:
            resolution = resolve_participants(ctx, spec, functions=functions)
        if resolution.todo or resolution.skipped:
            assert_participant_limit(
                len(resolution.todo) + len(resolution.skipped), limit, where=where
            )
            # manual 名单＝历史定格（加签扩员/驳回复活圈定，code-review 2026-10
            # P1-5）：不是表达式解析结果，不适用「单人节点多人＝错误」守卫——
            # 加签过的未声明 multi_instance 节点被驳回复活曾必然挂起
            manual_roster = bool(
                (getattr(ctx.token, "manual_participants", None) or {}).get(spec.code)
            ) if ctx.token is not None else False
            if (
                not manual_roster
                and spec.multi_instance is None
                and len(resolution.todo) > 1
            ):
                from flowork.engine.kernel import ParticipantCountMismatchError

                raise ParticipantCountMismatchError(
                    f"{where}（{spec.name or spec.code}）未声明会签（multi_instance）"
                    f"但解析出 {len(resolution.todo)} 名处理人：{resolution.todo}"
                    "——单人节点只允许一名；多人请显式声明会签或收敛取人表达式"
                )
            return resolution
        if spec.candidate_users or spec.candidate_groups:
            candidates = resolve_candidate_subjects(ctx, spec, functions)
            if candidates:
                assert_participant_limit(len(candidates), limit, where=f"{where}（候选槽）")
                return ParticipantResolution(candidates=candidates)
        if org is not None:
            return fallback_when_empty(ctx, spec, org)
        return resolution  # 空结果：行为层报「没有可用参与者」

    def handled_assignees(self, session: Session, token_id: str) -> list[str]:
        """令牌下轮次已消耗的人（轮次消耗行口径，spec §5.1 第十九轮）。

        审批类行：transfer（轮次被转手）/auto_pass（被策略跳过）也算消耗，
        不重派；协办/咨询行不算——此前会吞掉串行会签的下一棒（无声死锁）。
        """
        from sqlalchemy import select

        from flowork.persistence.orm import TaskHistoryRow
        from flowork.tasks.vote import consumed_turn_criteria

        stmt = select(TaskHistoryRow).where(
            TaskHistoryRow.token_id == token_id, *consumed_turn_criteria()
        )
        return [row.assignee for row in session.scalars(stmt)]

    def advance_after_task(
        self, session: Session, instance: ProcessInstanceRow, token: TokenRow
    ) -> None:
        """任务提交后的统一推进入口（自 kernel.py 迁入，第二十轮）。"""
        from typing import cast

        from flowork.engine.kernel import EngineExecutionError, Kernel
        from flowork.runtime.lifecycle import TokenState

        kernel = cast("Kernel", self)
        node = kernel.definition_of(session, instance).node(token.activity_code)
        if node is None:
            raise EngineExecutionError(f"令牌节点不存在：{token.activity_code}")
        kernel._maybe_complete(session, instance, node, token)
        # 出口不可达兜底（审计 D4，spec §4.1 第二十轮）：全部待办办结而令牌
        # 未完（两侧出口公式均未达成）＝技术故障——挂起+故障单，拒绝静默卡死。
        # kind 知识住行为（code-review 2026-10 P1-7：KIND_USER_TASK 分支违反
        # 硬约束 2）——owns_pending_tasks 声明「名下有待办域」：call_activity
        # 等子实例、wait 等消息名下本无待办；串行下一位已在 on_task_completed
        # （先行于本守卫）派发，不误报。
        behavior_cls = kernel.registry.behavior_cls(node.kind)
        if (
            getattr(behavior_cls, "owns_pending_tasks", False)
            and token.state == TokenState.UNFINISHED.value
            and not kernel.tasks(session, instance.id, token_id=token.id)
        ):
            raise EngineExecutionError(
                f"会签出口不可达：节点 {node.code} 全部待办已办结"
                f"（item_count={token.item_count}, approve={token.approve_count},"
                f" disapprove={token.disapprove_count}）而两侧出口公式均未达成"
                "——按技术故障挂起（spec §4.1 第二十轮）；处置：管理员手术或终态"
            )

    def _actor_context_of(self, actor: str) -> dict[str, Any]:
        """身份快照：完成事务内经目录端口定格操作者当时的岗位/角色/部门。

        目录未配置（裸跑/单测）→ {}；内核不解析、不校验、不理解内容——
        存什么、存多细是宿主的审计策略（spec §5.6）。
        """
        import json as _json

        org = self.engine.config.org if self.engine else None
        if org is None:
            return {}
        try:
            snapshot = dict(org.snapshot(actor) or {})
        except Exception:
            return {}
        if (
            len(_json.dumps(snapshot, ensure_ascii=False).encode("utf-8"))
            > self.MAX_ACTOR_CONTEXT_BYTES
        ):
            from flowork.engine.kernel import EngineExecutionError

            raise EngineExecutionError(
                f"身份快照超限（>{self.MAX_ACTOR_CONTEXT_BYTES}B）：宿主 snapshot 返回内容过大"
            )
        return snapshot

    def archive_task(
        self,
        session: Session,
        task: TaskRow,
        *,
        outcome: str,
        comment: str = "",
        actor: str = "system",
        idempotency_key: str | None = None,
        cause_token_id: str | None = None,
    ) -> None:
        """任务归档：写历史（append-only）并从活表删除。"""
        import uuid as _uuid

        from flowork.persistence.orm import TaskHistoryRow

        cost = (
            (self.config.now() - task.created_at).total_seconds()
            if task.created_at is not None
            else 0.0
        )
        session.add(
            TaskHistoryRow(
                id=_uuid.uuid4().hex,
                task_id=task.id,
                instance_id=task.instance_id,
                token_id=task.token_id,
                activity_code=task.activity_code,
                name=task.name,
                assignee=task.assignee,
                task_type=task.task_type,
                outcome=outcome,
                comment=comment,
                context_payload=task.context_payload,
                acted_by=actor,
                idempotency_key=idempotency_key,
                cause_token_id=cause_token_id,
                cost_seconds=cost,
                actor_context=self._actor_context_of(actor),
            )
        )
        session.delete(task)
        session.flush()
