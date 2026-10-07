"""RuntimeService：流程实例生命周期（发起 / 取消 / 查询）。"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError
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
    ) -> str:
        """发起流程（使用已发布定义的快照）。返回实例 ID。

        Args:
            code: 已发布流程定义编码。
            start_user: 发起人（参与者表达式 ``originator`` 解析为此值）。
            variables: 流程变量（表单数据/路由条件依据）。
            business_key: 业务关联键（如订单号），供外部对账。
            major/revision: 显式钉版本启动（spec §6.2）；缺省＝启动事务内
                SELECT 最新 published（deploy 前旧版、之后新版，变更边界＝deploy 时刻）。
        """
        result: str = self._engine.execute(
            lambda ctx: self._start(
                ctx, code, start_user, variables, business_key, None, None, major, revision
            )
        )
        return result

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
            )
        )
        return result

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
    ) -> str:
        import json

        from flowork.persistence.orm import ProcessInstanceRow as _Row

        snapshot = self._engine.repository.get_definition_json(code, major, revision)
        definition = self._engine.repository.get_definition(code, major, revision)
        instance = _Row(
            id=uuid.uuid4().hex,
            definition_code=code,
            definition_major=snapshot["major"],
            definition_revision=snapshot["revision"],
            definition_snapshot=json.dumps(snapshot, ensure_ascii=False),
            business_key=business_key,
            state="running",
            start_user=start_user,
            variables=dict(variables or {}),
            parent_id=parent_instance_id,
            parent_token_id=parent_token_id,
        )
        ctx.session.add(instance)
        ctx.session.flush()
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
        """修复 Incident：resume（恢复运行）/ cancel（取消实例）。"""

        def _command(ctx: CommandContext) -> Any:
            from flowork.persistence.orm import IncidentRow

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
            else:
                raise ValueError(f"未知修复动作：{action}（支持 resume/cancel）")
            incident.state = "resolved"
            incident.resolution = action

        self._engine.execute(_command)

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
