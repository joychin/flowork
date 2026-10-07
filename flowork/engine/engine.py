"""ProcessEngine：引擎入口与四服务装配（repository / runtime / task / history）。

用法::

    engine = ProcessEngine()                      # 默认内存 SQLite + 内置节点
    engine.repository.deploy(definition)          # 部署（校验＋版本判定＋发布）
    instance_id = engine.runtime.start_process("leave", start_user="u1")
    tasks = engine.task.list_tasks(assignee="u_manager")
    engine.task.complete(tasks[0].id, actor="u_manager")
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from flowork.engine.config import EngineConfig, resolve_db_url
from flowork.engine.history_service import HistoryService
from flowork.engine.kernel import Kernel
from flowork.engine.message_service import MessageService
from flowork.engine.ops import CommandContext, execute_command
from flowork.engine.repository_service import RepositoryService
from flowork.engine.runtime_service import RuntimeService
from flowork.engine.task_service import TaskService
from flowork.persistence.db import create_db_engine, create_schema, session_factory
from flowork.runtime.registry import HandlerRegistry
from flowork.tasks.behaviors import register_builtin_behaviors

__all__ = ["ProcessEngine"]

T = TypeVar("T")


class ProcessEngine:
    """流程引擎门面：组装持久化、注册表与四服务。"""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        register_builtin_behaviors(self.config.node_registry)
        self.handlers = HandlerRegistry()
        self._db_engine = create_db_engine(resolve_db_url(self.config))
        create_schema(self._db_engine)
        self.session_factory = session_factory(self._db_engine)
        self.repository = RepositoryService(self)
        self.runtime = RuntimeService(self)
        self.task = TaskService(self)
        self.history = HistoryService(self)
        self.messages = MessageService(self)
        from flowork.jobs.scheduler import JobScheduler

        self.jobs = JobScheduler(self)

    def execute(self, command: Callable[[CommandContext], T]) -> T:
        """经 Command 管线执行（拦截器：事务/乐观锁重试/事件落库/指标）。"""
        return execute_command(self, command)

    def kernel_for(self, ctx: CommandContext) -> Kernel:
        """为当前命令创建内核（事件写入命令缓冲，随事务落 outbox）。"""
        kernel = Kernel(
            config=self.config,
            registry=self.config.node_registry,
            handlers=self.handlers,
            events=ctx.events,
            engine=self,
        )
        kernel.command_ctx = ctx
        return kernel

    def register_handler(self, name: str, handler: Callable[..., None]) -> None:
        """注册声明式钩子/系统动作处理器（HandlerRegistry）。"""
        self.handlers.register(name, handler)

    def close(self) -> None:
        self._db_engine.dispose()
