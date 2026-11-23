"""管理员操作面（TaskService 组合件，入口 engine.task.* 不变）。

从 task_actions.py 拆出（单文件 ≤300 行回归）：
activate_node / cancel_node / finish_instance / remove_instance。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError
from flowork.events.domain import DomainEvent
from flowork.runtime.lifecycle import (
    TERMINAL_INSTANCE_STATES,
    UNSETTLED_INSTANCE_STATES,
    TokenState,
)

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
            # 终态守卫：终态实例上激活会造出
            # unfinished 令牌+ready 任务（状态与事件面背离、无人收口）——对齐
            # finish_instance 已有守卫；paused/suspended 按管理手术口径（D2）放行
            if instance.state in TERMINAL_INSTANCE_STATES:
                raise InstanceNotFoundError(
                    f"实例 {instance_id} 已终态（{instance.state}），不可激活节点"
                )
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            node = kernel.definition_of(ctx.session, instance).node(activity_code)
            assert node is not None, f"节点不存在：{activity_code}"
            manual = {activity_code: participants} if participants else None
            token = kernel.activate(ctx.session, instance, node, manual_participants=manual)
            kernel.record_event(
                DomainEvent.NODE_ACTIVATED_BY_ADMIN,
                {"activity_code": activity_code, "actor": actor},
                instance_id,
            )
            return token.id

        return self._engine.execute(_command)

    def cancel_node(self, instance_id: str, activity_code: str, actor: str) -> None:
        """管理员取消指定节点的全部未完令牌。

        两级事件：逐令牌 node_cancelled(by=admin, actor)=状态层；
        结尾一条 node_cancelled_by_admin=动作留痕层——不是重复信号。"""

        def _command(ctx: CommandContext) -> None:
            instance = self._instance(ctx, instance_id)
            # 终态守卫（同 activate_node）：终态实例无未完令牌，本命令只会
            # 发出无人对应的 node_cancelled_by_admin 动作事件
            if instance.state in TERMINAL_INSTANCE_STATES:
                raise InstanceNotFoundError(
                    f"实例 {instance_id} 已终态（{instance.state}），不可取消节点"
                )
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            tokens = kernel.tokens(ctx.session, instance_id, states=(TokenState.UNFINISHED.value,))
            for token in tokens:
                if token.activity_code == activity_code:
                    kernel.cancel_token(ctx.session, token, cause="admin", actor=actor)
            kernel.record_event(
                DomainEvent.NODE_CANCELLED_BY_ADMIN,
                {"activity_code": activity_code, "actor": actor},
                instance_id,
            )

        self._engine.execute(_command)

    def finish_instance(self, instance_id: str, actor: str) -> None:
        """管理员强制完成实例（未完令牌置 finished）。"""

        def _command(ctx: CommandContext) -> None:
            instance = self._instance(ctx, instance_id)
            # 终态守卫：complete_instance 对终态
            # 静默跳过但本命令事件无条件发——修前对已终态实例再调会发假
            # process_finished_by_admin（与 cancel/terminate 同类）
            if instance.state in TERMINAL_INSTANCE_STATES:
                raise InstanceNotFoundError(
                    f"实例 {instance_id} 已终态（{instance.state}），不可强制完成"
                )
            ctx.touch(instance)
            kernel = self._engine.kernel_for(ctx)
            from flowork.tasks.auto_pass import archive_auto_pass

            for token in kernel.tokens(
                ctx.session, instance_id, states=(TokenState.UNFINISHED.value,)
            ):
                for task in kernel.tasks(ctx.session, instance_id, token_id=token.id):
                    archive_auto_pass(
                        kernel,
                        ctx.session,
                        task,
                        comment="实例强制完成",
                        actor=actor,
                        reason="admin_finish",
                    )
                kernel.finish_token(ctx.session, token)
                # node_exited 补发：订阅方按
                # entered/exited 配对重建节点历史——修前该路径令牌有进无出
                kernel.record_event(
                    DomainEvent.NODE_EXITED,
                    {"activity_code": token.activity_code, "token_id": token.id},
                    instance_id,
                )
            kernel.complete_instance(ctx.session, instance, "completed")
            # 两级事件：process_completed=状态事实（终态契约三件
            # 套之一），本事件=管理动作留痕（end_status 标明落点）——不是双终态
            kernel.record_event(
                DomainEvent.PROCESS_FINISHED_BY_ADMIN,
                {"actor": actor, "end_status": "completed"},
                instance_id,
            )

        self._engine.execute(_command)

    def remove_instance(self, instance_id: str, actor: str) -> None:
        """删除实例及其全部运行时数据（含身份关联/Job/Incident，不可恢复）。

        有未结算子实例时拒绝：先 terminate 级联
        （或等子实例终态）再删——直接删父会留下继续运行的孤儿子实例，父子
        关系断裂不可恢复。这四张表（变量索引/变量历史/路由审计/消息订阅）
        与实例同删——此前残留成永久孤儿行（wait 订阅行令 waiting 反查失真）。
        整树删除：运行时数据按全树 id 删
        （修前只按根 id——已结算子孙的任务/令牌/审计残留孤儿行）；outbox
        只删已送达行，**未投递行保留待消费**（修前整删——订阅方丢事件、
        审计链断裂）。
        """

        def _command(ctx: CommandContext) -> None:
            from sqlalchemy import delete, select

            from flowork.persistence.orm import (
                CcItemRow,
                EventOutboxRow,
                IdentityLinkRow,
                IncidentRow,
                JobRow,
                MessageSubscriptionRow,
                ProcessInstanceRow,
                TaskHistoryRow,
                TaskRow,
                TokenRow,
            )
            from flowork.persistence.orm_v2 import (
                RouteDecisionRow,
                VariableHistoryRow,
                VariableIndexRow,
            )

            instance = ctx.session.get(ProcessInstanceRow, instance_id)
            if instance is None:
                raise InstanceNotFoundError(f"实例不存在：{instance_id}")
            # 整树收集（BFS）： unsettled 检查覆盖全深度——已结算直接子下仍可能
            # 挂着运行中的孙实例；删除时已结算子树整树同删（父子关系不悬空）
            descendants: list[ProcessInstanceRow] = []
            frontier = [instance_id]
            while frontier:
                rows = list(
                    ctx.session.scalars(
                        select(ProcessInstanceRow).where(ProcessInstanceRow.parent_id.in_(frontier))
                    )
                )
                descendants.extend(rows)
                frontier = [row.id for row in rows]
            unsettled = [row.id for row in descendants if row.state in UNSETTLED_INSTANCE_STATES]
            if unsettled:
                raise InstanceNotFoundError(
                    f"实例 {instance_id} 存在未结算子实例（{len(unsettled)} 个）"
                    "——先 terminate 级联或等子实例终态，再删除"
                )
            # 整树 id 集：运行时数据逐实例同删——修前只按根 id，
            # 已结算子孙的任务/令牌/审计/事件残留孤儿行（与 docstring 相悖）
            tree_ids = [instance_id] + [row.id for row in descendants]
            # 身份关联经 task_id 挂靠，先于任务删除
            ctx.session.execute(
                delete(IdentityLinkRow).where(
                    IdentityLinkRow.task_id.in_(
                        select(TaskRow.id).where(TaskRow.instance_id.in_(tree_ids))
                    )
                )
            )
            for row_type in (
                TaskRow,
                TaskHistoryRow,
                TokenRow,
                CcItemRow,
                JobRow,
                VariableIndexRow,
                VariableHistoryRow,
                RouteDecisionRow,
            ):
                ctx.session.execute(delete(row_type).where(row_type.instance_id.in_(tree_ids)))
            # 订阅行只删未消费的（message_id IS NULL）：已 match 的行就是
            # message_id 全局防重集（修前整删＝
            # remove 后同 id 重发被重新入缓冲，下一个等待者二次消费同一逻辑消息）
            ctx.session.execute(
                delete(MessageSubscriptionRow).where(
                    MessageSubscriptionRow.instance_id.in_(tree_ids),
                    MessageSubscriptionRow.message_id.is_(None),
                )
            )
            # outbox 只删已送达行：未投递行保留待消费——事件是跨实例
            # 的审计事实，整删会让订阅方永远收不到已发生的事
            ctx.session.execute(
                delete(EventOutboxRow).where(
                    EventOutboxRow.instance_id.in_(tree_ids),
                    EventOutboxRow.delivered_at.is_not(None),
                )
            )
            ctx.session.execute(delete(IncidentRow).where(IncidentRow.instance_id.in_(tree_ids)))
            for row in descendants:  # 已结算子树整树同删
                ctx.session.delete(row)
            ctx.session.delete(instance)

        self._engine.execute(_command)
