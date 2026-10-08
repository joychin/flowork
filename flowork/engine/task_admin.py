"""管理员操作面（TaskService 组合件，入口 engine.task.* 不变）。

从 task_actions.py 拆出（单文件 ≤300 行回归）：
activate_node / cancel_node / finish_instance / remove_instance。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError
from flowork.runtime.lifecycle import TokenState

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["TaskAdminMixin"]


class TaskAdminMixin:
    """管理员手术操作；宿主 TaskService 提供 _engine/_instance。"""

    _engine: ProcessEngine

    def _instance(self, ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
        """实例加载由 TaskOpsMixin 提供。"""
        raise NotImplementedError

    def activate_node(
        self,
        instance_id: str,
        activity_code: str,
        actor: str,
        participants: list[str] | None = None,
    ) -> str:
        """管理员激活任意节点（可指定参与者），返回新令牌 ID。"""

        def _command(ctx: CommandContext) -> str:
            instance = self._instance(ctx, instance_id)
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            node = kernel.definition_of(ctx.session, instance).node(activity_code)
            assert node is not None, f"节点不存在：{activity_code}"
            manual = {activity_code: participants} if participants else None
            token = kernel.activate(ctx.session, instance, node, manual_participants=manual)
            kernel.record_event(
                "node_activated_by_admin",
                {"activity_code": activity_code, "actor": actor},
                instance_id,
            )
            return token.id

        return self._engine.execute(_command)

    def cancel_node(self, instance_id: str, activity_code: str, actor: str) -> None:
        """管理员取消指定节点的全部未完令牌。"""

        def _command(ctx: CommandContext) -> None:
            ctx.touch(self._instance(ctx, instance_id))
            kernel = self._engine.kernel_for(ctx)
            tokens = kernel.tokens(ctx.session, instance_id, states=(TokenState.UNFINISHED.value,))
            for token in tokens:
                if token.activity_code == activity_code:
                    kernel.cancel_token(ctx.session, token)
            kernel.record_event(
                "node_cancelled_by_admin",
                {"activity_code": activity_code, "actor": actor},
                instance_id,
            )

        self._engine.execute(_command)

    def finish_instance(self, instance_id: str, actor: str) -> None:
        """管理员强制完成实例（未完令牌置 finished）。"""

        def _command(ctx: CommandContext) -> None:
            instance = self._instance(ctx, instance_id)
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            for token in kernel.tokens(
                ctx.session, instance_id, states=(TokenState.UNFINISHED.value,)
            ):
                for task in kernel.tasks(ctx.session, instance_id, token_id=token.id):
                    kernel.archive_task(
                        ctx.session,
                        task,
                        outcome="auto_pass",
                        comment="实例强制完成",
                        actor=actor,
                    )
                kernel.finish_token(ctx.session, token)
            kernel.complete_instance(ctx.session, instance, "completed")
            kernel.record_event("process_finished_by_admin", {"actor": actor}, instance_id)

        self._engine.execute(_command)

    def remove_instance(self, instance_id: str, actor: str) -> None:
        """删除实例及其全部运行时数据（含身份关联/Job/Incident，不可恢复）。"""

        def _command(ctx: CommandContext) -> None:
            from sqlalchemy import delete, select

            from flowork.persistence.orm import (
                CcItemRow,
                EventOutboxRow,
                IdentityLinkRow,
                IncidentRow,
                JobRow,
                ProcessInstanceRow,
                TaskHistoryRow,
                TaskRow,
                TokenRow,
            )

            # 身份关联经 task_id 挂靠，先于任务删除
            ctx.session.execute(
                delete(IdentityLinkRow).where(
                    IdentityLinkRow.task_id.in_(
                        select(TaskRow.id).where(TaskRow.instance_id == instance_id)
                    )
                )
            )
            for row_type in (TaskRow, TaskHistoryRow, TokenRow, CcItemRow, JobRow):
                ctx.session.execute(delete(row_type).where(row_type.instance_id == instance_id))
            ctx.session.execute(
                delete(EventOutboxRow).where(EventOutboxRow.instance_id == instance_id)
            )
            ctx.session.execute(delete(IncidentRow).where(IncidentRow.instance_id == instance_id))
            instance = ctx.session.get(ProcessInstanceRow, instance_id)
            if instance is None:
                raise InstanceNotFoundError(f"实例不存在：{instance_id}")
            ctx.session.delete(instance)

        self._engine.execute(_command)
