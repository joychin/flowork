"""节点定义基类（Spec 层）：纯数据、可 JSON 序列化。

- ``kind`` 是 NodeRegistry 的键：内置类型见 flowork.definitions.builtin，
  自定义类型继承 FlowNode 声明自己的字段后注册即可（含校验与序列化）。
- ``properties`` 是 BPMN extensionElements 等价的扩展袋，供 UI/外部系统存放附加配置。
- 行为不在此层：见 flowork.runtime.behavior（Spec/Behavior 分离）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["FlowNode", "HandlerRef", "MultiInstance", "NodeHooks"]


class HandlerRef(BaseModel):
    """声明式钩子引用：按名从 HandlerRegistry 解析，params 随调用传入。"""

    model_config = ConfigDict(extra="forbid")

    handler: str
    params: dict[str, Any] = Field(default_factory=dict)


class NodeHooks(BaseModel):
    """节点生命周期钩子的声明式绑定（层 1 扩展；全代码扩展见 NodeBehavior）。"""

    model_config = ConfigDict(extra="forbid")

    on_enter: HandlerRef | None = None
    on_exit: HandlerRef | None = None
    on_cancel: HandlerRef | None = None
    on_fail: HandlerRef | None = None


class MultiInstance(BaseModel):
    """会签（多人参与）配置。

    mode：parallel=并签（全员同时待办）/ sequential=串行（逐人待办）。
    出口为比例表达式字符串，M2 由出口判定器求值（如 ">=50%"、"all"、">=1"）。
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["parallel", "sequential"] = "parallel"
    approve_exit: str | None = None
    disapprove_exit: str | None = None


class TimeoutPolicy(BaseModel):
    """超时策略：due_minutes 到期后执行 strategy（notify/auto_approve/auto_reject）。"""

    model_config = ConfigDict(extra="forbid")

    due_minutes: int
    strategy: str = "notify"


class FlowNode(BaseModel):
    """所有流程节点定义的基类。

    子类（含自定义类）以具名字段获得类型校验；未声明字段保留在模型扩展字段中，
    保证模板 JSON 往返无损。
    """

    model_config = ConfigDict(extra="allow")

    code: str
    name: str = ""
    kind: str
    hooks: NodeHooks = Field(default_factory=NodeHooks)
    properties: dict[str, Any] = Field(default_factory=dict)
