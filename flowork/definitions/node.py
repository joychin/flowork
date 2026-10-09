"""节点定义基类（Spec 层）：纯数据、可 JSON 序列化。

- ``kind`` 是 NodeRegistry 的键：内置类型见 flowork.definitions.builtin，
  自定义类型继承 FlowNode 声明自己的字段后注册即可（含校验与序列化）。
- ``properties`` 是 BPMN extensionElements 等价的扩展袋，供 UI/外部系统存放附加配置。
- 行为不在此层：见 flowork.runtime.behavior（Spec/Behavior 分离）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DeadlineAction",
    "FlowNode",
    "HandlerRef",
    "MultiInstance",
    "NodeHooks",
    "TaskDeadline",
    "TimeoutPolicy",
]


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
    出口为 **CEL 布尔公式**（spec §4.2 位点统一），上下文注入 task.* 会签计数
    （item_count/approve_count/disapprove_count，spec C.4）；缺省＝全员
    （approve_exit 全员同意 / disapprove_exit 全员否决）。旧专用语法等价：
    "all"→task.approve_count >= task.item_count；">=N%"→整数乘法
    task.approve_count * 100 >= task.item_count * N。
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["parallel", "sequential"] = "parallel"
    approve_exit: str | None = None
    disapprove_exit: str | None = None


class TimeoutPolicy(BaseModel):
    """超时策略（旧形态，v2.2 由 deadlines 列表取代；validate/diff 兼容读取）。"""

    model_config = ConfigDict(extra="forbid")

    due_minutes: int
    strategy: str = "notify"


class DeadlineAction(StrEnum):
    """超时动作五枚举（v2.2 spec §5.1，对齐 WS-HumanTask escalation 形态）。"""

    NOTIFY = "notify"
    REASSIGN = "reassign"
    ESCALATE = "escalate"
    AUTO_APPROVE = "auto_approve"
    AUTO_REJECT = "auto_reject"


class TaskDeadline(BaseModel):
    """单条超时动作：进入节点 after_minutes 分钟后执行 action（自进入起算）。"""

    model_config = ConfigDict(extra="forbid")

    after_minutes: int = Field(gt=0)
    action: DeadlineAction = DeadlineAction.NOTIFY
    # reassign 改派目标：CEL 表达式（fire 时求值，task.* 可用——spec §4.2/C.4）
    reassign_to: str | None = None
    # escalate 升级目标：CEL 表达式，*必填*（v2.3 第十四轮，spec §5.1）——
    # 引擎不再自带「上级」概念（组织模型归宿主，spec §5.5 分工铁律）；
    # 科层组织的图纸写法：escalate_to='manager_of(task.assignee)'
    escalate_to: str | None = None


def effective_deadlines(spec: Any) -> list[TaskDeadline]:
    """节点的生效超时列表：deadlines 优先；空则旧 timeout 字段一一映射（兼容）。"""
    deadlines = list(getattr(spec, "deadlines", None) or [])
    if deadlines:
        return sorted(deadlines, key=lambda d: d.after_minutes)
    timeout = getattr(spec, "timeout", None)
    if timeout is None or timeout.strategy not in (
        "notify",
        "auto_approve",
        "auto_reject",
    ):
        return []
    return [TaskDeadline(after_minutes=timeout.due_minutes, action=timeout.strategy)]


class FlowNode(BaseModel):
    """所有流程节点定义的基类。

    子类（含自定义类）以具名字段获得类型校验；未声明字段保留在模型扩展字段中，
    保证模板 JSON 往返无损。
    """

    model_config = ConfigDict(extra="allow")

    code: str
    name: str = ""
    kind: str
    # —— 开始条件（推进策略，对齐 对标产品 preCondition ALL/ANY）——
    # 仅多条入边节点有意义：any=任一前置分支到达即开始（缺省，竞速并取消兄弟挂起分支）；
    # all=所有前置分支到齐再开始（与汇聚）。cc_task 恒按 any 执行。
    advance_policy: Literal["any", "all"] | None = None
    hooks: NodeHooks = Field(default_factory=NodeHooks)
    properties: dict[str, Any] = Field(default_factory=dict)
