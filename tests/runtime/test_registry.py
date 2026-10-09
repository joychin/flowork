"""注册表测试：注册/解析/未知 kind/自定义类型经 Spec 类校验（扩展机制核心）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flowork.definitions.builtin import (
    KIND_BRANCH,
    KIND_CALL_ACTIVITY,
    KIND_CC_TASK,
    KIND_END,
    KIND_SERVICE_TASK,
    KIND_START,
    KIND_USER_TASK,
)
from flowork.definitions.node import FlowNode
from flowork.definitions.wait_node import KIND_WAIT
from flowork.errors import UnknownHandler, UnknownNodeKind
from flowork.runtime.behavior import NodeBehavior
from flowork.runtime.registry import HandlerRegistry, NodeRegistry, create_registry


class PingSpec(FlowNode):
    kind: str = "ping"
    message: str = ""


class PingBehavior(NodeBehavior):
    def on_enter(self, ctx) -> None:  # type: ignore[no-untyped-def]
        ctx.variables["ping"] = self.spec.code


def test_builtin_kinds_registered():
    """内置 kind 恰为 KIND_* 常量全集（相等而非超集：新增 kind 必须显式过此清单）。"""
    registry = create_registry()
    assert set(registry.kinds()) == {
        KIND_START,
        KIND_END,
        KIND_USER_TASK,
        KIND_SERVICE_TASK,
        KIND_CALL_ACTIVITY,
        KIND_CC_TASK,
        KIND_BRANCH,
        KIND_WAIT,
    }


def test_register_and_resolve_custom_kind(registry: NodeRegistry):
    registry.register("ping", PingSpec, PingBehavior)
    assert "ping" in registry
    assert registry.spec_cls("ping") is PingSpec
    assert registry.behavior_cls("ping") is PingBehavior


def test_unknown_kind_raises_with_hint():
    registry = create_registry()
    with pytest.raises(UnknownNodeKind) as excinfo:
        registry.spec_cls("nope")
    assert "nope" in str(excinfo.value)
    assert "user_task" in str(excinfo.value)  # 提示已知 kind


def test_duplicate_register_requires_replace(registry: NodeRegistry):
    registry.register("ping", PingSpec)
    with pytest.raises(ValueError, match="已注册"):
        registry.register("ping", PingSpec)
    registry.register("ping", PingSpec, PingBehavior, replace=True)  # 不抛即通过


def test_custom_spec_field_validation(registry: NodeRegistry):
    """自定义类型的字段在加载路径获得类型校验。"""
    registry.register("ping", PingSpec)
    spec = registry.spec_cls("ping").model_validate(
        {"code": "p1", "kind": "ping", "message": "hello"}
    )
    assert isinstance(spec, PingSpec)
    assert spec.message == "hello"
    with pytest.raises(ValidationError):
        registry.spec_cls("ping").model_validate({"code": "p2", "kind": "ping", "message": 123})


def test_behavior_defaults_are_noop():
    behavior = NodeBehavior(PingSpec(code="x", kind="ping"))
    assert behavior.try_complete(object()) is True  # type: ignore[arg-type]


def test_handler_registry_roundtrip():
    handlers = HandlerRegistry()
    handlers.register("log", lambda ctx, params: None)
    assert callable(handlers.resolve("log"))
    assert "log" in handlers.names()
    with pytest.raises(UnknownHandler):
        handlers.resolve("missing")
