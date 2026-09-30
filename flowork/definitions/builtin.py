"""内置节点类型：与自定义类型走同一注册机制（dogfooding，边界 G4）。

内置 kind：start / end / user_task / service_task / call_activity / cc_task / gateway。
M0 仅注册 Spec（Behavior 自 M1 起随引擎接入补齐）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from flowork.definitions.node import FlowNode, MultiInstance, TimeoutPolicy

if TYPE_CHECKING:
    from flowork.runtime.registry import NodeRegistry

__all__ = [
    "KIND_CALL_ACTIVITY",
    "KIND_CC_TASK",
    "KIND_END",
    "KIND_GATEWAY",
    "KIND_SERVICE_TASK",
    "KIND_START",
    "KIND_USER_TASK",
    "CallActivitySpec",
    "CcTaskSpec",
    "EndSpec",
    "GatewayKind",
    "GatewaySpec",
    "ServiceTaskSpec",
    "StartSpec",
    "UserTaskSpec",
    "register_builtin_nodes",
]

KIND_START: Literal["start"] = "start"
KIND_END: Literal["end"] = "end"
KIND_USER_TASK: Literal["user_task"] = "user_task"
KIND_SERVICE_TASK: Literal["service_task"] = "service_task"
KIND_CALL_ACTIVITY: Literal["call_activity"] = "call_activity"
KIND_CC_TASK: Literal["cc_task"] = "cc_task"
KIND_GATEWAY: Literal["gateway"] = "gateway"


class GatewayKind(StrEnum):
    """网关类型：EXCLUSIVE 排他（条件出边+默认边）/ PARALLEL 并行 / INCLUSIVE 相容。

    汇聚语义由网关类型决定（EXCLUSIVE 单进即过、PARALLEL 等全到、
    INCLUSIVE 等已激活分支到齐并取消其余）——取代 对标产品 的隐式 preCondition。
    """

    EXCLUSIVE = "exclusive"
    PARALLEL = "parallel"
    INCLUSIVE = "inclusive"


class StartSpec(FlowNode):
    kind: Literal["start"] = KIND_START


class EndSpec(FlowNode):
    kind: Literal["end"] = KIND_END


class GatewaySpec(FlowNode):
    kind: Literal["gateway"] = KIND_GATEWAY
    gateway_kind: GatewayKind = GatewayKind.EXCLUSIVE


class UserTaskSpec(FlowNode):
    """人工审批节点。参与者表达式完整解析在 M4（OrgDirectory + 取人函数），
    M1 起支持显式用户 id 与发起人标记 ``originator``。
    """

    kind: Literal["user_task"] = KIND_USER_TASK
    assignee: str | None = None
    assignees: list[str] = Field(default_factory=list)
    candidate_users: list[str] = Field(default_factory=list)
    candidate_groups: list[str] = Field(default_factory=list)
    multi_instance: MultiInstance | None = None
    reject_target: str | None = None
    # —— 参与者策略（M4）：命中者自动通过（auto_pass 历史并计入同意数）——
    auto_pass_originator: bool = False
    auto_pass_previous: bool = False
    auto_pass_participated: bool = False
    no_participant_policy: str = "raise"  # raise / to_admins
    timeout: TimeoutPolicy | None = None


class ServiceTaskSpec(FlowNode):
    """系统节点：执行 HandlerRegistry 中注册的动作。"""

    kind: Literal["service_task"] = KIND_SERVICE_TASK
    action: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class CallActivitySpec(FlowNode):
    """子流程调用：process_code 指向被调流程定义；数据映射 M5 完整实现。"""

    kind: Literal["call_activity"] = KIND_CALL_ACTIVITY
    process_code: str = ""
    is_synchronous: bool = True
    input_mappings: dict[str, str] = Field(default_factory=dict)
    output_mappings: dict[str, str] = Field(default_factory=dict)


class CcTaskSpec(FlowNode):
    """传阅节点：recipients 为接收人（M4 支持表达式，M1+ 支持显式 id）。"""

    kind: Literal["cc_task"] = KIND_CC_TASK
    recipients: list[str] = Field(default_factory=list)


def register_builtin_nodes(registry: NodeRegistry) -> None:
    """把全部内置类型注册进注册表（与自定义类型同一入口）。

    ``ui`` 为设计器展示元数据：name 显示名 / category 调色板分组 /
    shape 画布形状 / color 主色 / palette 调色板预设项（含创建时的字段默认值）。
    """
    registry.register(
        KIND_START,
        StartSpec,
        ui={
            "name": "开始",
            "category": "事件",
            "shape": "circle",
            "color": "#16a34a",
            "icon": "play",
            "palette": [{"label": "开始", "defaults": {}}],
        },
    )
    registry.register(
        KIND_END,
        EndSpec,
        ui={
            "name": "结束",
            "category": "事件",
            "shape": "circle",
            "color": "#dc2626",
            "icon": "stop",
            "palette": [{"label": "结束", "defaults": {}}],
        },
    )
    registry.register(
        KIND_GATEWAY,
        GatewaySpec,
        ui={
            "name": "条件分支",
            "category": "分支",
            "shape": "diamond",
            "color": "#d97706",
            "icon": "gateway",
            "palette": [
                {"label": "条件分支", "defaults": {"gateway_kind": "exclusive"}},
                {"label": "并行分支(同时分合)", "defaults": {"gateway_kind": "parallel"}},
            ],
        },
    )
    registry.register(
        KIND_USER_TASK,
        UserTaskSpec,
        ui={
            "name": "审批任务",
            "category": "任务",
            "shape": "rect",
            "color": "#2563eb",
            "icon": "user",
            "palette": [
                {
                    "label": "审批任务",
                    "defaults": {
                        "assignee": None,
                        "multi_instance": {"mode": "parallel"},
                        "timeout": {"due_minutes": 1440, "strategy": "notify"},
                    },
                }
            ],
        },
    )
    registry.register(
        KIND_SERVICE_TASK,
        ServiceTaskSpec,
        ui={
            "name": "服务任务",
            "category": "自动化",
            "shape": "rect",
            "color": "#7c3aed",
            "icon": "settings",
            "palette": [{"label": "服务任务", "defaults": {"action": ""}}],
        },
    )
    registry.register(
        KIND_CALL_ACTIVITY,
        CallActivitySpec,
        ui={
            "name": "子流程",
            "category": "自动化",
            "shape": "rect",
            "color": "#475569",
            "icon": "box",
            "palette": [{"label": "子流程", "defaults": {"process_code": ""}}],
        },
    )
    registry.register(
        KIND_CC_TASK,
        CcTaskSpec,
        ui={
            "name": "传阅",
            "category": "任务",
            "shape": "rect",
            "color": "#0d9488",
            "icon": "mail",
            "palette": [{"label": "传阅", "defaults": {"recipients": []}}],
        },
    )
