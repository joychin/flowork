"""RuntimeService：流程实例生命周期（发起 / 取消 / 查询）。"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from flowork.engine.kernel import Kernel
from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError
from flowork.events.domain import DomainEvent
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
    ) -> str:
        """发起流程（使用已发布定义的快照）。返回实例 ID。

        Args:
            code: 已发布流程定义编码。
            start_user: 发起人（参与者表达式 ``originator`` 解析为此值）。
            variables: 流程变量（表单数据/路由条件依据）。
            business_key: 业务关联键（如订单号），供外部对账。
        """
        result: str = self._engine.execute(
            lambda ctx: self._start(ctx, code, start_user, variables, business_key, None, None)
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
                ctx, code, start_user, variables, None, parent_instance_id, parent_token_id
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
    ) -> str:
        import json

        from flowork.persistence.orm import ProcessInstanceRow as _Row

        snapshot = self._engine.repository.get_definition_json(code)
        definition = self._engine.repository.get_definition(code)
        instance = _Row(
            id=uuid.uuid4().hex,
            definition_code=code,
            definition_version=snapshot["version"],
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
            kernel = self._engine.kernel_for(ctx)
            states = (TokenState.UNFINISHED.value,)
            for token in kernel.tokens(ctx.session, instance_id, states=states):
                kernel.cancel_token(ctx.session, token)
            kernel.complete_instance(ctx.session, instance, "cancelled")
            kernel.record_event(
                DomainEvent.PROCESS_CANCELLED, {"actor": actor, "reason": reason}, instance_id
            )

        self._engine.execute(_command)

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
        definition_version=instance.definition_version,
        business_key=instance.business_key,
        state=instance.state,
        start_user=instance.start_user,
        started_at=instance.started_at,
        finished_at=instance.finished_at,
        parent_id=instance.parent_id,
    )


_ = Kernel  # Kernel 在 kernel_for 装配处使用
