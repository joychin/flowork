"""NodeBehavior：节点运行策略基类（生命周期钩子契约）。

行为（Behavior）与定义（Spec）分离：
- Spec 在 flowork.definitions，纯数据、可 JSON 序列化；
- Behavior 承载流转语义，经 NodeRegistry 注册后由引擎按 kind 解析。

编写自定义节点：
1. 继承 FlowNode 声明定义字段（可校验、可序列化）；
2. 继承 NodeBehavior 覆写需要的钩子；
3. ``node_registry.register(kind, spec_cls, behavior_cls)`` —— 引擎核心零改动。
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from flowork.definitions.node import FlowNode

__all__ = ["InMemoryNodeContext", "NodeBehavior", "NodeContext"]


@runtime_checkable
class NodeContext(Protocol):
    """行为与钩子可见的最小上下文。

    引擎在 M1 提供正式实现；测试与示例可使用 InMemoryNodeContext。
    """

    @property
    def node(self) -> FlowNode: ...

    variables: MutableMapping[str, Any]

    def info(self, message: str) -> None:
        """记录结构化日志（附节点编码）。"""

    def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """发出领域事件（M1 起写入 wf_event_outbox）。"""


class NodeBehavior:
    """节点行为基类：钩子全部有默认空实现，按需覆写。

    刻意不使用 ABC/abstractmethod——部分扩展只想覆写个别钩子，
    行为契约由文档、类型注解与测试共同约束。

    硬不变量：on_enter/on_exit/on_cancel/on_fail 只可观察与写 ``ctx.variables``，
    不得改变路由（路由决策只属于引擎 + 条件求值）。
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


class InMemoryNodeContext:
    """内存上下文：M0 阶段驱动行为/钩子的测试与示例载体。"""

    def __init__(self, node: FlowNode, variables: Mapping[str, Any] | None = None) -> None:
        self.node = node
        self.variables: MutableMapping[str, Any] = dict(variables or {})
        self.logs: list[str] = []
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, message: str) -> None:
        self.logs.append(f"[{self.node.code}] {message}")

    def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.events.append((event_name, dict(payload)))
