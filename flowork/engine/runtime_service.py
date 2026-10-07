"""RuntimeService：流程实例生命周期（发起 / 取消 / 查询）。"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from flowork.engine.ops import CommandContext
from flowork.errors import DuplicateStartError, InstanceNotFoundError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import ProcessInstanceRow
from flowork.runtime.lifecycle import TokenState
from flowork.tasks.models import InstanceInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["RuntimeService"]


class RuntimeService:
    """实例运行时服务。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine

    def start_process(
        self,
        code: str,
        *,
        start_user: str,
        variables: dict[str, Any] | None = None,
        business_key: str | None = None,
        major: int | None = None,
        revision: int | None = None,
        idempotency_key: str | None = None,
        reuse: Literal["reject", "return_existing", "allow_duplicate"] = "reject",
    ) -> str:
        """发起流程（使用已发布定义的快照）。返回实例 ID。

        Args:
            code: 已发布流程定义编码。
            start_user: 发起人（参与者表达式 ``originator`` 解析为此值）。
            variables: 流程变量（表单数据/路由条件依据）。
            business_key: 业务关联键（如订单号），供外部对账。
            major/revision: 显式钉版本启动（spec §6.2）；缺省＝启动事务内
                SELECT 最新 published（deploy 前旧版、之后新版，变更边界＝deploy 时刻）。
            idempotency_key/reuse: 启动幂等三策略（spec §11）——reject（撞 key 报
                DuplicateStartError，默认）/ return_existing（返回已有实例）/
                allow_duplicate（明示放弃幂等）。key 与 definition_code 联合唯一，
                并发撞车由唯一索引兜底后按策略收口。
        """

        def _command(ctx: CommandContext) -> str:
            return self._start(
                ctx,
                code,
                start_user,
                variables,
                business_key,
                None,
                None,
                major,
                revision,
                idempotency_key,
                reuse,
            )

        try:
            return self._engine.execute(_command)
        except IntegrityError as exc:
            # 并发同 key 撞联合唯一索引：败者事务已回滚，新事务重查后按策略收口
            if idempotency_key is None or reuse == "allow_duplicate":
                raise
            existing = self._engine.execute(
                lambda ctx: self._existing_instance(ctx, code, idempotency_key)
            )
            if existing is None:
                raise
            if reuse == "reject":
                raise DuplicateStartError(
                    f"启动幂等键冲突（并发）：{code}#{idempotency_key} 已有实例 {existing}"
                ) from exc
            return existing

    def _start_child(
        self,
        *,
        code: str,
        start_user: str,
        variables: dict[str, Any] | None,
        parent_instance_id: str,
        parent_token_id: str,
    ) -> str:
        result: str = self._engine.execute(
            lambda ctx: self._start(
                ctx,
                code,
                start_user,
                variables,
                None,
                parent_instance_id,
                parent_token_id,
                None,
                None,
                None,
                "reject",
            )
        )
        return result

    def _existing_instance(self, ctx: CommandContext, code: str, key: str) -> str | None:
        """按 (definition_code, idempotency_key) 查已有实例。"""
        from sqlalchemy import select

        from flowork.persistence.orm import ProcessInstanceRow as _Row

        found = ctx.session.scalars(
            select(_Row.id).where(_Row.definition_code == code, _Row.idempotency_key == key)
        ).first()
        return found

    def _start(
        self,
        ctx: CommandContext,
        code: str,
        start_user: str,
        variables: dict[str, Any] | None,
        business_key: str | None,
        parent_instance_id: str | None,
        parent_token_id: str | None,
        major: int | None,
        revision: int | None,
        idempotency_key: str | None,
        reuse: Literal["reject", "return_existing", "allow_duplicate"],
    ) -> str:
        import json

        from flowork.persistence.orm import ProcessInstanceRow as _Row

        if idempotency_key is not None and reuse != "allow_duplicate":
            existing = self._existing_instance(ctx, code, idempotency_key)
            if existing is not None:
                if reuse == "reject":
                    raise DuplicateStartError(
                        f"启动幂等键冲突：{code}#{idempotency_key} 已有实例 {existing}"
                    )
                return existing

        snapshot = self._engine.repository.get_definition_json(code, major, revision)
        definition = self._engine.repository.get_definition(code, major, revision)
        # input 契约边界①：发起时按 schema 拒收（spec §3.2，报错不入库）
        from flowork.definitions.contract import validate_input_contract

        validate_input_contract(definition.input, dict(variables or {}), boundary="start")
        instance = _Row(
            id=uuid.uuid4().hex,
            definition_code=code,
            definition_major=snapshot["major"],
            definition_revision=snapshot["revision"],
            definition_snapshot=json.dumps(snapshot, ensure_ascii=False),
            business_key=business_key,
            # allow_duplicate＝明示放弃幂等：不落 key（联合唯一索引下同 key 只能一行）
            idempotency_key=idempotency_key if reuse != "allow_duplicate" else None,
            state="running",
            start_user=start_user,
            variables=dict(variables or {}),
            parent_id=parent_instance_id,
            parent_token_id=parent_token_id,
        )
        ctx.session.add(instance)
        # v2.2（spec §7）：实例时限物化——按 after_minutes 折算 deadline_at（自然时间）
        if definition.deadline is not None:
            from datetime import timedelta

            from flowork.persistence.orm import JobRow

            instance.deadline_at = self._engine.config.now() + timedelta(
                minutes=definition.deadline.after_minutes
            )
        ctx.session.flush()
        if definition.deadline is not None:
            import uuid as _uuid

            from flowork.persistence.orm import JobRow

            ctx.session.add(
                JobRow(
                    id=_uuid.uuid4().hex,
                    job_type="timer",
                    state="scheduled",
                    run_at=instance.deadline_at,
                    instance_id=instance.id,
                    payload={
                        "kind": "instance_deadline",
                        "strategy": definition.deadline.strategy.value,
                    },
                )
            )
        kernel = self._engine.kernel_for(ctx)
        kernel.record_event(
            DomainEvent.PROCESS_STARTED,
            {"definition_code": code, "start_user": start_user, "business_key": business_key},
            instance.id,
        )
        start_node = definition.start_node()
        if start_node is None:
            raise InstanceNotFoundError(f"定义 {code} 缺少 start 节点")
        kernel.activate(ctx.session, instance, start_node)
        return instance.id

    def pause_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """暂停实例（spec §7）：任务不可办、timer 冻结；恢复后按自然时长顺延。"""

        def _command(ctx: CommandContext) -> Any:
            instance = _load(ctx, instance_id)
            if instance.state != "running":
                raise InstanceNotFoundError(
                    f"实例 {instance_id} 状态 {instance.state!r} 不可暂停（须 running）"
                )
            ctx.touch(instance)
            instance.state = "paused"
            instance.paused_at = self._engine.config.now()
            kernel = self._engine.kernel_for(ctx)
            kernel.record_event(
                DomainEvent.PROCESS_PAUSED, {"actor": actor, "reason": reason}, instance_id
            )

        self._engine.execute(_command)

    def resume_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """恢复暂停实例：未完 timer 的 run_at 顺延暂停时长（工作日历默认自然时间）。"""

        def _command(ctx: CommandContext) -> Any:
            from datetime import timedelta

            from flowork.persistence.orm import JobRow

            instance = _load(ctx, instance_id)
            if instance.state != "paused":
                raise InstanceNotFoundError(
                    f"实例 {instance_id} 状态 {instance.state!r} 不可恢复（须 paused）"
                )
            ctx.touch(instance)
            now = self._engine.config.now()
            paused_minutes = 0
            if instance.paused_at is not None:
                paused_minutes = int((now - instance.paused_at).total_seconds() // 60)
            instance.state = "running"
            instance.paused_at = None
            for job in ctx.session.scalars(
                select(JobRow).where(JobRow.instance_id == instance_id, JobRow.state == "scheduled")
            ):
                job.run_at = job.run_at + timedelta(minutes=paused_minutes)
            kernel = self._engine.kernel_for(ctx)
            kernel.record_event(
                DomainEvent.PROCESS_RESUMED,
                {"actor": actor, "reason": reason, "deferred_minutes": paused_minutes},
                instance_id,
            )

        self._engine.execute(_command)

    def terminate_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """终止实例（spec §7）：管理员强杀——撤销令牌与待办，终态 terminated。"""

        def _command(ctx: CommandContext) -> Any:
            instance = _load(ctx, instance_id)
            if instance.state in ("completed", "cancelled", "terminated"):
                raise InstanceNotFoundError(f"实例 {instance_id} 已终态（{instance.state}）")
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            states = (TokenState.UNFINISHED.value,)
            for token in kernel.tokens(ctx.session, instance_id, states=states):
                kernel.cancel_token(ctx.session, token)
            kernel.complete_instance(ctx.session, instance, "terminated")
            kernel.record_event(
                DomainEvent.PROCESS_TERMINATED, {"actor": actor, "reason": reason}, instance_id
            )

        self._engine.execute(_command)

    def cancel_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """取消实例：撤销全部未完令牌与待办。"""

        def _command(ctx: CommandContext) -> Any:
            instance = _load(ctx, instance_id)
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            states = (TokenState.UNFINISHED.value,)
            for token in kernel.tokens(ctx.session, instance_id, states=states):
                kernel.cancel_token(ctx.session, token)
            kernel.complete_instance(ctx.session, instance, "cancelled")
            kernel.record_event(
                DomainEvent.PROCESS_CANCELLED, {"actor": actor, "reason": reason}, instance_id
            )

        self._engine.execute(_command)

    # —— Incident（M5）——;

    def _record_incident(self, instance_ids: list[str], error: Exception) -> None:
        """引擎错误 → Incident 落库 + 实例挂起（独立命令）。"""
        self._engine.execute(lambda ctx: self._record_incident_in_ctx(ctx, instance_ids, error))

    def _record_incident_in_ctx(
        self, ctx: CommandContext, instance_ids: list[str], error: Exception
    ) -> None:
        """同事务记录 Incident（供命令管线与调度器等复合命令复用）。"""

        import uuid as _uuid

        from flowork.persistence.orm import IncidentRow

        kernel = self._engine.kernel_for(ctx)
        for instance_id in instance_ids:
            ctx.session.add(
                IncidentRow(
                    id=_uuid.uuid4().hex,
                    instance_id=instance_id,
                    message=str(error),
                    error_class=type(error).__name__,
                )
            )
            instance = ctx.session.get(ProcessInstanceRow, instance_id)
            if instance is not None and instance.state == "running":
                instance.state = "suspended"
            kernel.record_event(
                "incident_raised",
                {"message": str(error), "error_class": type(error).__name__},
                instance_id,
            )

    def list_incidents(self, instance_id: str | None = None) -> list[dict[str, Any]]:
        """查询 Incident（open/resolved）。"""

        def _command(ctx: CommandContext) -> Any:
            from sqlalchemy import select

            from flowork.persistence.orm import IncidentRow

            stmt = select(IncidentRow).order_by(IncidentRow.created_at.desc())
            if instance_id:
                stmt = stmt.where(IncidentRow.instance_id == instance_id)
            return [
                {
                    "id": row.id,
                    "instance_id": row.instance_id,
                    "message": row.message,
                    "error_class": row.error_class,
                    "state": row.state,
                    "resolution": row.resolution,
                }
                for row in ctx.session.scalars(stmt)
            ]

        incidents: list[dict[str, Any]] = self._engine.execute(_command)
        return incidents

    def resolve_incident(
        self, incident_id: str, action: str = "resume", actor: str = "admin"
    ) -> None:
        """修复 Incident（spec §8/P1-4，根因没修好允许复发）。

        - resume：恢复运行（轻症——外部环境已恢复）；
        - cancel：取消实例（业务侧放弃）；
        - retry：重派——该实例 dead-letter Job 重置 scheduled，实例恢复；
        - skip：跳过节点——故障节点的未完令牌视为完成并按路由推进（绕放行门）；
        - fail：终止实例（管理员判定不可恢复，终态 terminated）。
        """

        def _command(ctx: CommandContext) -> Any:
            from flowork.persistence.orm import IncidentRow, JobRow

            incident = ctx.session.get(IncidentRow, incident_id)
            if incident is None or incident.state != "open":
                raise InstanceNotFoundError(f"Incident 不存在或已处理：{incident_id}")
            if action == "resume":
                instance = ctx.session.get(ProcessInstanceRow, incident.instance_id)
                if instance is not None:
                    ctx.touch(instance)
                    instance.state = "running"
            elif action == "cancel":
                self.cancel_process(incident.instance_id, actor=actor, reason="incident:cancel")
            elif action == "retry":
                moment = self._engine.config.now()
                for job in ctx.session.scalars(
                    select(JobRow).where(
                        JobRow.instance_id == incident.instance_id,
                        JobRow.state == "dead_letter",
                    )
                ):
                    job.state = "scheduled"
                    job.run_at = moment
                    job.next_retry_at = None
                    job.worker_id = None
                    job.lease_until = None
                instance = ctx.session.get(ProcessInstanceRow, incident.instance_id)
                if instance is not None:
                    ctx.touch(instance)
                    instance.state = "running"
            elif action == "skip":
                self._skip_failed_nodes(ctx, incident.instance_id, actor)
            elif action == "fail":
                self.terminate_process(incident.instance_id, actor=actor, reason="incident:fail")
            else:
                raise ValueError(f"未知修复动作：{action}（支持 resume/cancel/retry/skip/fail）")
            incident.state = "resolved"
            incident.resolution = action

        self._engine.execute(_command)

    def _skip_failed_nodes(self, ctx: CommandContext, instance_id: str, actor: str) -> None:
        """跳过故障节点：未完令牌视为完成，按条件路由激活后继（管理员手术，绕放行门）。"""
        from flowork.engine.routing import select_outgoing

        kernel = self._engine.kernel_for(ctx)
        instance = ctx.session.get(ProcessInstanceRow, instance_id)
        assert instance is not None
        definition = kernel.definition_of(ctx.session, instance)
        for token in kernel.tokens(ctx.session, instance_id, states=(TokenState.UNFINISHED.value,)):
            node = definition.node(token.activity_code)
            kernel.cancel_token(ctx.session, token)
            kernel.record_event(
                DomainEvent.NODE_CANCELLED,
                {"activity_code": token.activity_code, "by": "incident:skip", "actor": actor},
                instance_id,
            )
            if node is None:
                continue
            outgoing = select_outgoing(
                definition.outgoing(node.code), dict(instance.variables), mode="all_match"
            )
            for flow in outgoing:
                target = definition.node(flow.target)
                if target is not None:
                    kernel.activate(ctx.session, instance, target, source_activity_code=node.code)
        if instance.state == "suspended":
            instance.state = "running"
        ctx.touch(instance)

    def move_token(self, token_id: str, target_activity: str, actor: str = "admin") -> str:
        """管理员修复：把未完令牌直移到目标节点（返回新令牌 ID）。"""

        def _command(ctx: CommandContext) -> Any:
            from flowork.persistence.orm import TokenRow

            token = ctx.session.get(TokenRow, token_id)
            if token is None or token.state != "unfinished":
                raise InstanceNotFoundError(f"令牌不存在或已完成：{token_id}")
            instance = ctx.session.get(ProcessInstanceRow, token.instance_id)
            assert instance is not None
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            definition = kernel.definition_of(ctx.session, instance)
            target = definition.node(target_activity)
            assert target is not None, f"目标节点不存在：{target_activity}"
            kernel.cancel_token(ctx.session, token)
            new_token = kernel.activate(
                ctx.session, instance, target, source_activity_code=token.activity_code
            )
            kernel.record_event(
                "token_moved_by_admin",
                {"from": token.activity_code, "to": target_activity, "actor": actor},
                instance.id,
            )
            return new_token.id

        token_id_new: str = self._engine.execute(_command)
        return token_id_new

    def list_instances(
        self,
        *,
        definition_code: str | None = None,
        major: int | None = None,
        revision: int | None = None,
        state: str | None = None,
    ) -> list[InstanceInfo]:
        """实例清单（可按定义版本过滤——「多少在跑实例钉旧版」的查询，spec §6.5）。"""

        def _command(ctx: CommandContext) -> Any:
            stmt = select(ProcessInstanceRow).order_by(ProcessInstanceRow.started_at.desc())
            if definition_code is not None:
                stmt = stmt.where(ProcessInstanceRow.definition_code == definition_code)
            if major is not None:
                stmt = stmt.where(ProcessInstanceRow.definition_major == major)
            if revision is not None:
                stmt = stmt.where(ProcessInstanceRow.definition_revision == revision)
            if state is not None:
                stmt = stmt.where(ProcessInstanceRow.state == state)
            return [_to_info(row) for row in ctx.session.scalars(stmt)]

        result: list[InstanceInfo] = self._engine.execute(_command)
        return result

    def get_instance(self, instance_id: str) -> InstanceInfo:
        def _command(ctx: CommandContext) -> Any:
            return _to_info(_load(ctx, instance_id))

        result: InstanceInfo = self._engine.execute(_command)
        return result


def _load(ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
    from flowork.persistence.orm import ProcessInstanceRow as _Row

    instance = ctx.session.get(_Row, instance_id)
    if instance is None:
        raise InstanceNotFoundError(f"流程实例不存在：{instance_id}")
    return instance


def _to_info(instance: ProcessInstanceRow) -> InstanceInfo:
    return InstanceInfo(
        id=instance.id,
        definition_code=instance.definition_code,
        definition_major=instance.definition_major,
        definition_revision=instance.definition_revision,
        business_key=instance.business_key,
        state=instance.state,
        start_user=instance.start_user,
        started_at=instance.started_at,
        finished_at=instance.finished_at,
        parent_id=instance.parent_id,
    )
