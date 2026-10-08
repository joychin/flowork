"""NodeBehavior：节点运行策略基类（生命周期钩子契约）。

行为（Behavior）与定义（Spec）分离：
- Spec 在 flowork.definitions，纯数据、可 JSON 序列化；
- Behavior 承载流转语义，经 NodeRegistry 注册后由引擎按 kind 解析。

编写自定义节点：
1. 继承 FlowNode 声明定义字段（可校验、可序列化）；
2. 继承 NodeBehavior 覆写需要的钩子；
3. ``node_registry.register(kind, spec_cls, behavior_cls)`` —— 引擎核心零改动。

上下文：钩子统一接收 ``NodeContext``；引擎实际传入 ``RuntimeNodeContext``
（含内核服务）。需要内核服务（建任务/完成实例等）的行为在钩子内以
``assert isinstance(ctx, RuntimeNodeContext)`` 收窄类型（类型与运行时双保险）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from flowork.definitions.flow import SequenceFlow

if TYPE_CHECKING:
    from flowork.definitions.node import FlowNode

__all__ = ["InMemoryNodeContext", "NodeBehavior", "NodeContext"]


class NodeContext:
    """行为与钩子上下文基类：node / variables / info / emit。"""

    node: FlowNode
    variables: dict[str, Any]

    def __init__(self, node: FlowNode, variables: dict[str, Any] | None = None) -> None:
        self.node = node
        if variables is not None:
            self.variables = variables  # 子类以 property 覆写时不传入

    @property
    def kernel(self) -> Any:
        """内核服务（仅引擎运行上下文提供；内存上下文访问即报错）。"""
        raise NotImplementedError("内核服务仅 RuntimeNodeContext 提供")

    def info(self, message: str) -> None:  # pragma: no cover - 由子类实现
        """记录结构化日志（附节点编码）。"""
        raise NotImplementedError

    def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:  # pragma: no cover
        """发出领域事件（写入事件 outbox）。"""
        raise NotImplementedError


class NodeBehavior:
    """节点行为基类：钩子全部有默认空实现，按需覆写。

    刻意不使用 ABC/abstractmethod——部分扩展只想覆写个别钩子，
    行为契约由文档、类型注解与测试共同约束。

    硬不变量：on_enter/on_exit/on_cancel/on_fail 只可观察与写 ``ctx.variables``，
    不得改变路由（路由决策只属于 get_outgoing + 条件求值 + 引擎）。
    """

    def __init__(self, spec: FlowNode) -> None:
        self.spec = spec

    def on_enter(self, ctx: NodeContext) -> None:
        """进入节点（令牌创建后）。

        典型用途：UserTask 生成待办、AI 节点调用模型、ServiceTask 执行动作。
        """

    def try_complete(self, ctx: NodeContext) -> bool:
        """拉取式完成判定：返回 True 时引擎调用 on_exit 并推进路由。

        默认进入即完成（如 start/end/连接类节点）。
        """
        return True

    def on_exit(self, ctx: NodeContext) -> None:
        """节点完成、令牌离站前。"""

    def on_cancel(self, ctx: NodeContext) -> None:
        """节点被取消（并行分支取消/撤回/实例取消）。"""

    def on_fail(self, ctx: NodeContext, error: Exception) -> None:
        """节点异常（引擎随后置 FAILED 并创建 Incident）。"""

    def on_task_completed(self, ctx: NodeContext, task_id: str, outcome: str) -> None:
        """本节点的任务提交后（UserTask 家族：串行会签推进等）。"""

    def get_outgoing(self, ctx: NodeContext) -> list[SequenceFlow]:
        """路由出口（命中的都走）：无条件边全走 + 条件真者走，默认边兜底。

        命中即走（只走第一条真条件）由 BranchBehavior(FIRST_MATCH) 覆写；
        多条入边的等待/竞速由 kernel 推进门（advance_policy）决定，不经本钩子。
        """
        from flowork.engine.route_audit import RoutingAudit, persist_route_decision
        from flowork.engine.routing import evaluation_variables, select_outgoing
        from flowork.expr.context import expression_functions_of
        from flowork.tasks.facts import attach_node

        definition = ctx.kernel.definition_of(ctx.session, ctx.instance)  # type: ignore[attr-defined]
        outgoing = definition.outgoing(ctx.node.code)
        engine = getattr(ctx.kernel, "engine", None)
        audit = RoutingAudit()
        selected = select_outgoing(
            outgoing,
            evaluation_variables(ctx.instance, ctx.variables, session=ctx.session),  # type: ignore[attr-defined]
            mode="all_match",
            functions=attach_node(
                expression_functions_of(getattr(engine, "config", None)),
                ctx.session,  # type: ignore[attr-defined]
                ctx.instance.id,  # type: ignore[attr-defined]
                node_calls=audit.node_calls,
            ),
            audit=audit,
        )
        persist_route_decision(
            ctx.session,  # type: ignore[attr-defined]
            ctx.instance.id,  # type: ignore[attr-defined]
            ctx.token.id if ctx.token else None,  # type: ignore[attr-defined]
            audit,
        )
        return selected


class InMemoryNodeContext(NodeContext):
    """内存上下文：测试与示例驱动行为/钩子的轻量载体（无内核服务）。"""

    def __init__(self, node: FlowNode, variables: Mapping[str, Any] | None = None) -> None:
        super().__init__(node, dict(variables or {}))
        self.logs: list[str] = []
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, message: str) -> None:
        self.logs.append(f"[{self.node.code}] {message}")

    def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.events.append((event_name, dict(payload)))
