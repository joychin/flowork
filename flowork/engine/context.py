"""引擎运行时节点上下文：行为（Behavior）与内核交互的唯一通道。

继承 NodeContext（node / variables / info / emit），并暴露内核服务
（建任务、完成实例、令牌查询等）供内置行为使用；自定义行为只依赖
基类子集即可移植（见 examples/ai_review_demo.py）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from flowork.events.domain import DomainEvent
from flowork.runtime.behavior import NodeContext

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.node import FlowNode
    from flowork.engine.kernel import Kernel
    from flowork.persistence.orm import ProcessInstanceRow, TaskRow, TokenRow

__all__ = ["RuntimeNodeContext"]


class RuntimeNodeContext(NodeContext):
    """一次节点生命周期（激活/完成判定）内的上下文（引擎传入行为钩子）。"""

    def __init__(
        self,
        kernel: Kernel,
        session: Session,
        instance: ProcessInstanceRow,
        node: FlowNode,
        token: TokenRow | None,
    ) -> None:
        super().__init__(node)
        # variables 直接引用实例行字典：原地写经 MutableDict 追踪持久化
        self.variables = instance.variables
        self._kernel = kernel
        self.session = session
        self.instance = instance
        self.token = token
        self._logs: list[str] = []

    # —— NodeContext 协议 ——

    @property
    def kernel(self) -> Kernel:
        return self._kernel

    def info(self, message: str) -> None:
        self._logs.append(f"[{self.node.code}] {message}")

    def emit(self, event_name: str | DomainEvent, payload: Mapping[str, Any]) -> None:
        name = event_name.value if isinstance(event_name, DomainEvent) else str(event_name)
        self.kernel.record_event(name, dict(payload), self.instance.id)

    # —— 内核服务（内置行为可用）——

    def create_task(
        self,
        *,
        assignee: str,
        name: str = "",
        task_type: str = "approval",
        context_payload: Mapping[str, Any] | None = None,
        root_task_id: str | None = None,
        emit_event: bool = True,
    ) -> TaskRow:
        assert self.token is not None, "无令牌节点不能创建任务"
        return self.kernel.create_task(
            self.session,
            self.instance,
            self.token,
            self.node,
            assignee=assignee,
            name=name or self.node.name or self.node.code,
            task_type=task_type,
            context_payload=dict(context_payload or {}),
            root_task_id=root_task_id,
            emit_event=emit_event,
        )

    def complete_instance(self, end_status: str = "completed") -> None:
        self.kernel.complete_instance(self.session, self.instance, end_status)

    def open_tasks(self) -> list[TaskRow]:
        token_id = self.token.id if self.token else None
        return self.kernel.tasks(self.session, self.instance.id, token_id=token_id)

    def token_updated(self) -> None:
        if self.token is not None:
            self.kernel.touch_token(self.session, self.token)
