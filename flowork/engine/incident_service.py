"""故障单（Incident）：记录、清单、五动作处置、move_token 管理员手术
（v2 spec §8 错误二分 / P1-4）。RuntimeService 经 mixin 组合，入口不变。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from flowork.engine.instance_lifecycle import _to_info
from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import ProcessInstanceRow
from flowork.runtime.lifecycle import TokenState
from flowork.tasks.models import InstanceInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["IncidentMixin"]


class IncidentMixin:
    """RuntimeService 的故障处置面。"""

    _engine: ProcessEngine

    def cancel_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """由宿主类提供（InstanceLifecycleMixin.cancel_process）。"""
        raise NotImplementedError

    def terminate_process(self, instance_id: str, actor: str, reason: str = "") -> None:
        """由宿主类提供（InstanceLifecycleMixin.terminate_process）。"""
        raise NotImplementedError

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
        from flowork.engine.route_audit import RoutingAudit, persist_route_decision
        from flowork.engine.routing import evaluation_variables, select_outgoing
        from flowork.expr.context import expression_functions_of
        from flowork.tasks.facts import attach_node

        kernel = self._engine.kernel_for(ctx)
        instance = ctx.session.get(ProcessInstanceRow, instance_id)
        assert instance is not None
        definition = kernel.definition_of(ctx.session, instance)
        base_functions = expression_functions_of(self._engine.config)
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
            audit = RoutingAudit()
            outgoing = select_outgoing(
                definition.outgoing(node.code),
                evaluation_variables(
                    instance, instance.variables, session=ctx.session, now=ctx.engine.config.now()
                ),
                mode="all_match",
                functions=attach_node(
                    base_functions, ctx.session, instance.id, node_calls=audit.node_calls
                ),
                audit=audit,
            )
            persist_route_decision(ctx.session, instance_id, token.id, audit)
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
