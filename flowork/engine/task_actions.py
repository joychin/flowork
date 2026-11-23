"""任务操作集核心：认领 / 转办 + 共享助手。

以 Mixin 形式并入 TaskService（保持四服务 API：engine.task.*）。
传阅/协办/加签撤回/管理员操作拆至 task_cc.py / task_assist.py / task_rework.py /
task_admin.py（入口不变；TaskService 组合顺序见 engine/service 组装处）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.ops import CommandContext
from flowork.errors import InstanceNotFoundError, TaskNotAvailableError
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import TaskRow
from flowork.runtime.lifecycle import InstanceState
from flowork.tasks.actions import allowed_actions, assert_action_allowed
from flowork.tasks.deadlines import carry_deadline_jobs
from flowork.tasks.models import TaskInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow

_TaskRowT = TaskRow  # 历史别名（_ready_task 返回类型注解用）

__all__ = ["TaskOpsMixin"]


class TaskOpsMixin:
    """任务操作集：方法经 Command 管线执行，全部带 actor 归因。"""

    _engine: ProcessEngine  # 由 TaskService.__init__ 提供

    # —— 认领（候选任务）——

    def claim(self, task_id: str, user: str) -> TaskInfo:
        """候选人认领任务：assignee 置为本人（候选关联保留——谁本来有资格是审计信息）。"""
        result: TaskInfo = self._engine.execute(lambda ctx: self._claim(ctx, task_id, user))
        return result

    def _claim(self, ctx: CommandContext, task_id: str, user: str) -> TaskInfo:
        from flowork.tasks.candidates import person_key, require_candidate

        task = ctx.session.get(TaskRow, task_id)
        if task is None or task.state != "ready":
            raise TaskNotAvailableError(f"任务不可认领（不存在或已处理）：{task_id}")
        self._assert_operable(ctx, task)
        instance = self._instance(ctx, task.instance_id)
        ctx.touch(instance)
        org = self._engine.config.org
        if task.assignee == "":
            if not instance.simulated:
                require_candidate(ctx.session, task_id, user, org)
            # 模拟实例放行：候选是业务防抢注语义，不约束试跑（模拟任务已被
            # 查询面隔离，驱动者以 simulator 身份认领办理）
            task.assignee = user
            ctx.session.flush()
            self._emit_task_event(ctx, DomainEvent.TASK_CLAIMED, task, user)
            return self._task_info(task)
        if person_key(task.assignee) != person_key(user):  # 已被认领/直派
            raise TaskNotAvailableError(f"任务已被 {task.assignee} 认领")
        return self._task_info(task)

    def unclaim(self, task_id: str, user: str) -> TaskInfo:
        """取消认领：任务回到候选池。"""
        result: TaskInfo = self._engine.execute(lambda ctx: self._unclaim(ctx, task_id, user))
        return result

    def _unclaim(self, ctx: CommandContext, task_id: str, user: str) -> TaskInfo:
        from flowork.tasks.candidates import candidate_links

        task = ctx.session.get(TaskRow, task_id)
        if task is None or task.state != "ready" or task.assignee != user:
            raise TaskNotAvailableError(f"任务不可取消认领：{task_id}")
        self._assert_operable(ctx, task)
        if not candidate_links(ctx.session, task_id):
            # 直派任务没有候选池可回：置空后 claim 需
            # 命中候选、complete 需先认领——任务将永久无人能办且不出故障单
            raise TaskNotAvailableError(f"任务 {task_id} 为直接指派（无候选池），不可取消认领")
        ctx.touch(self._instance(ctx, task.instance_id))
        task.assignee = ""
        ctx.session.flush()
        # 对称补齐：claim 有事件、unclaim 无——候选池回流
        # 对订阅方不可见。assignee 记取消认领人（回池前状态）。
        kernel = self._engine.kernel_for(ctx)
        kernel.record_event(
            DomainEvent.TASK_UNCLAIMED,
            {
                "task_id": task.id,
                "activity_code": task.activity_code,
                "task_type": task.task_type,
                "assignee": user,
                "actor": user,
            },
            task.instance_id,
        )
        return self._task_info(task)

    # —— 转办 ——

    def transfer(self, task_id: str, to_user: str, actor: str, comment: str = "") -> TaskInfo:
        """转办：当前任务归档（outcome=transfer），同令牌为 to_user 生成新待办。"""
        result: TaskInfo = self._engine.execute(
            lambda ctx: self._transfer(ctx, task_id, to_user, actor, comment)
        )
        return result

    def _transfer(
        self, ctx: CommandContext, task_id: str, to_user: str, actor: str, comment: str
    ) -> TaskInfo:
        task = self._ready_task(ctx, task_id, action="transfer")
        ctx.touch(self._instance(ctx, task.instance_id))
        kernel = self._engine.kernel_for(ctx)
        self._emit_task_event(ctx, DomainEvent.TASK_TRANSFERRED, task, actor)
        new_task = self._clone_task(
            ctx, task, assignee=to_user, operator=actor, root=task.root_task_id or task.id
        )
        carry_deadline_jobs(ctx.session, task, new_task)
        kernel.archive_task(ctx.session, task, outcome="transfer", comment=comment, actor=actor)
        return self._task_info(new_task)

    # —— 协办 ——

    def _instance(self, ctx: CommandContext, instance_id: str) -> ProcessInstanceRow:
        from flowork.persistence.orm import ProcessInstanceRow as _Row

        instance = ctx.session.get(_Row, instance_id)
        if instance is None:
            raise InstanceNotFoundError(f"实例不存在：{instance_id}")
        return instance

    def _ready_task(
        self,
        ctx: CommandContext,
        task_id: str,
        expected_type: str | None = None,
        *,
        action: str | None = None,
    ) -> _TaskRowT:
        task = ctx.session.get(TaskRow, task_id)
        if task is None or task.state != "ready":
            raise TaskNotAvailableError(f"任务不可操作（不存在或已处理）：{task_id}")
        self._assert_operable(ctx, task, action=action)
        if expected_type and task.task_type != expected_type:
            raise TaskNotAvailableError(f"任务类型不符（期望 {expected_type}）：{task_id}")
        return task

    def _assert_operable(
        self, ctx: CommandContext, task: TaskRow, *, action: str | None = None
    ) -> None:
        """统一守卫：实例态 + 动作表 + 版本抢占复验。

        action 传入时按 (状态×类型×认领态) 动作表判定（tasks/actions.py）；不传
        保持仅实例态守卫（withdraw 等特殊路径自带专属守卫）。
        """
        instance = self._instance(ctx, task.instance_id)
        if instance.state == InstanceState.SUSPENDED.value:
            raise TaskNotAvailableError("实例已挂起（Incident 待修复），不可操作")
        if instance.state == InstanceState.PAUSED.value:
            raise TaskNotAvailableError("实例已暂停，恢复后才可操作")
        if action is not None:
            assert_action_allowed(action, task)
        self._claim_task_action(ctx, task, instance)

    def _claim_task_action(
        self, ctx: CommandContext, task: TaskRow, instance: ProcessInstanceRow
    ) -> None:
        """动作抢占咽喉：touch 后立即 flush（版本守卫 UPDATE 抢占），再重读任务复验。

        pysqlite 的 SELECT 各自最新快照——「读任务→读实例」间隙对手提交会使实例行
        读到新版本、提交期版本守卫失效（双 agree，tests/edge submit vs timer 实证）。
        抢占开启的事务能看到对手已提交的删行 → ObjectDeletedError 归一领域错误。
        """
        from sqlalchemy.orm.exc import ObjectDeletedError

        from flowork.engine.task_consult import assert_no_open_consults

        assert_no_open_consults(ctx, task)  # 咨询软挂起
        ctx.touch(instance)
        ctx.session.flush()
        ctx.session.expire(task)
        try:
            state = task.state
        except ObjectDeletedError as exc:
            raise TaskNotAvailableError(f"任务已被处理：{task.id}") from exc
        if state != "ready":
            raise TaskNotAvailableError(f"任务不可操作（不存在或已处理）：{task.id}")

    def _clone_task(
        self,
        ctx: CommandContext,
        task: TaskRow,
        *,
        assignee: str,
        operator: str,
        task_type: str | None = None,
        root: str | None = None,
    ) -> TaskRow:
        import uuid

        new_task = TaskRow(
            created_at=self._engine.config.now(),  # 与原任务同源时钟
            id=uuid.uuid4().hex,
            instance_id=task.instance_id,
            token_id=task.token_id,
            activity_code=task.activity_code,
            name=task.name,
            assignee=assignee,
            task_type=task_type or task.task_type,
            priority=task.priority,  # 转办/加签/协办后优先级不回落默认
            context_payload=dict(task.context_payload),
            root_task_id=root,
        )
        ctx.session.add(new_task)
        ctx.session.flush()
        # actor=发起克隆的人（修前误记被指派人）
        self._emit_task_event(ctx, DomainEvent.TASK_CREATED, new_task, operator)
        return new_task

    def _task_info(self, task: TaskRow) -> TaskInfo:
        return TaskInfo(
            id=task.id,
            instance_id=task.instance_id,
            token_id=task.token_id,
            activity_code=task.activity_code,
            name=task.name,
            assignee=task.assignee,
            task_type=task.task_type,
            state=task.state,
            context_payload=task.context_payload,
            priority=task.priority,
            due_at=task.due_at,
            created_at=task.created_at,
            available_actions=allowed_actions(task),
        )

    def _emit_task_event(
        self, ctx: CommandContext, event: DomainEvent, task: TaskRow, actor: str
    ) -> None:
        """任务事件统一载荷：全部带 activity_code/task_type。

        此前该面只有 task_id/assignee/actor——消费者定位节点须反查历史表，
        且转办克隆的 task_created 与主路径形状不一致；统一后同名事件同形。
        """
        from flowork.persistence.orm import ProcessInstanceRow

        kernel = self._engine.kernel_for(ctx)
        instance = ctx.session.get(ProcessInstanceRow, task.instance_id)
        kernel.record_event(
            event,
            {
                "task_id": task.id,
                "activity_code": task.activity_code,
                "task_type": task.task_type,
                "assignee": task.assignee,
                "actor": actor,
                # 与主路径 create_task 载荷同形（通知集成够用面）
                "name": task.name,
                "due_at": task.due_at.isoformat() if task.due_at else None,
                "definition_code": instance.definition_code if instance else None,
                "business_key": instance.business_key if instance else None,
                "start_user": instance.start_user if instance else None,
            },
            task.instance_id,
        )
