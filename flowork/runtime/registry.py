"""注册表：节点类型与处理器的唯一组装点（扩展机制核心）。

新增一种节点类型 = 一次 ``register(kind, spec_cls, behavior_cls)`` 调用，
引擎核心对 kind 零 if/else（边界文档 M1 评审项）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from flowork.errors import UnknownHandler, UnknownNodeKind

if TYPE_CHECKING:
    from flowork.definitions.node import FlowNode
    from flowork.runtime.behavior import NodeBehavior

__all__ = ["HandlerRegistry", "NodeRegistration", "NodeRegistry", "create_registry"]


@dataclass(frozen=True)
class NodeRegistration:
    """一条节点类型注册项。"""

    kind: str
    spec: type[FlowNode]
    behavior: type[NodeBehavior] | None = None
    # 设计器 UI 元数据：name/icon/category/shape/color/palette（纯展示，引擎不消费）。
    # 缺省时设计器按 kind 兜底渲染——引擎新增节点类型，前端零代码跟进。
    ui: dict[str, Any] = field(default_factory=dict)


class NodeRegistry:
    """节点类型注册表：kind → (Spec 类, Behavior 类)。"""

    def __init__(self) -> None:
        self._kinds: dict[str, NodeRegistration] = {}

    def register(
        self,
        kind: str,
        spec: type[FlowNode],
        behavior: type[NodeBehavior] | None = None,
        *,
        replace: bool = False,
        ui: dict[str, Any] | None = None,
    ) -> None:
        """注册节点类型；同 kind 重复注册需显式 ``replace=True``。

        ``ui`` 为设计器展示元数据（name/icon/category/shape/color/palette），
        可省略——设计器对缺省项按通用样式兜底。
        """
        if not kind:
            raise ValueError("kind 不能为空")
        if kind in self._kinds and not replace:
            raise ValueError(f"节点类型 kind={kind!r} 已注册（如需覆盖请 replace=True）")
        if ui is None and kind in self._kinds:
            # replace 重注册（如 Behavior 接入）不传 ui 时保留原元数据
            ui = self._kinds[kind].ui or {}
        self._kinds[kind] = NodeRegistration(kind=kind, spec=spec, behavior=behavior, ui=ui or {})

    def registration(self, kind: str) -> NodeRegistration:
        try:
            return self._kinds[kind]
        except KeyError:
            raise UnknownNodeKind(kind, self.kinds()) from None

    def spec_cls(self, kind: str) -> type[FlowNode]:
        return self.registration(kind).spec

    def behavior_cls(self, kind: str) -> type[NodeBehavior] | None:
        return self.registration(kind).behavior

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._kinds)

    def __contains__(self, kind: str) -> bool:
        return kind in self._kinds


class HandlerRegistry:
    """处理器注册表：名字 → 可调用（声明式钩子 HandlerRef 按名解析）。"""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[..., None]] = {}

    def register(self, name: str, handler: Callable[..., None], *, replace: bool = False) -> None:
        if not name:
            raise ValueError("处理器名不能为空")
        if name in self._handlers and not replace:
            raise ValueError(f"处理器 name={name!r} 已注册（如需覆盖请 replace=True）")
        self._handlers[name] = handler

    def resolve(self, name: str) -> Callable[..., None]:
        try:
            return self._handlers[name]
        except KeyError:
            raise UnknownHandler(name, self.names()) from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._handlers)


def create_registry() -> NodeRegistry:
    """创建已注册全部内置节点类型的新注册表。"""
    from flowork.definitions.builtin import register_builtin_nodes

    registry = NodeRegistry()
    register_builtin_nodes(registry)
    return registry
