"""内置节点类型：与自定义类型走同一注册机制（dogfooding，边界 G4）。

内置 kind：start/end/user_task/service_task/call_activity/cc_task/branch/wait
（wait 定义在 wait_node.py，本文件挂 300 行红线拆出）。

用户词汇（对齐现代工作流产品，无「网关」）：
- 条件分支（branch）：出边挂条件，route_mode 决定匹配方式；
- 同时进行：任意节点多条无条件出边即并行，无需专门节点；
- 开始条件（advance_policy）：多条入边 any/all；汇聚语义在目标节点上。
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from flowork.definitions.node import (
    FlowNode,
    MultiInstance,
    TaskDeadline,
    TimeoutPolicy,
)
from flowork.definitions.wait_node import (
    KIND_WAIT,
    WaitMessage,
    WaitSpec,
    WaitTimeout,
    register_wait_node,
)

if TYPE_CHECKING:
    from flowork.runtime.registry import NodeRegistry

__all__ = [
    "KIND_BRANCH",
    "KIND_CALL_ACTIVITY",
    "KIND_CC_TASK",
    "KIND_END",
    "KIND_SERVICE_TASK",
    "KIND_START",
    "KIND_USER_TASK",
    "KIND_WAIT",
    "BranchSpec",
    "CallActivitySpec",
    "CcTaskSpec",
    "EndSpec",
    "RouteMode",
    "ServiceTaskSpec",
    "StartSpec",
    "UserTaskSpec",
    "WaitMessage",
    "WaitSpec",
    "WaitTimeout",
    "register_builtin_nodes",
]

KIND_START: Literal["start"] = "start"
KIND_END: Literal["end"] = "end"
KIND_USER_TASK: Literal["user_task"] = "user_task"
KIND_SERVICE_TASK: Literal["service_task"] = "service_task"
KIND_CALL_ACTIVITY: Literal["call_activity"] = "call_activity"
KIND_CC_TASK: Literal["cc_task"] = "cc_task"
KIND_BRANCH: Literal["branch"] = "branch"


class RouteMode(StrEnum):
    """条件分支的匹配方式（分支侧语义；汇聚侧见 FlowNode.advance_policy）。

    - FIRST_MATCH 命中即走：按出边顺序取第一条条件为真者；全不中走默认边。
    - ALL_MATCH 命中的都走：条件为真的边全部激活（对标产品 原生语义）；皆空走默认边。
    """

    FIRST_MATCH = "first_match"
    ALL_MATCH = "all_match"


class StartSpec(FlowNode):
    kind: Literal["start"] = KIND_START


class EndSpec(FlowNode):
    kind: Literal["end"] = KIND_END


class BranchSpec(FlowNode):
    """条件分支：纯路由锚点（无副作用），出边挂条件。

    存在价值：决策落点（审计日志）、静态校验锚点、AI 安全操作对象。
    并行分流不需要本节点——多条无条件出边即同时进行。
    """

    kind: Literal["branch"] = KIND_BRANCH
    route_mode: RouteMode = RouteMode.FIRST_MATCH


class UserTaskSpec(FlowNode):
    """人工审批节点。assignee/assignees 每项为 CEL 表达式（spec §4.2 位点统一）：
    字面人写 ``'u_a'``，发起人为保留标识符 ``initiator``，取人函数如
    ``manager_of(initiator)``（词汇经 EngineConfig.expression_functions 注册）。
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
    # v2.2（spec §5.1）：超时动作列表（多级预警/改派/升级/自动通过·驳回），
    # 同节点上限 8 条（validator 把关）；旧 timeout 为其退化形态
    deadlines: list[TaskDeadline] = Field(default_factory=list)


class ServiceTaskSpec(FlowNode):
    """系统节点：执行 HandlerRegistry 中注册的动作。"""

    kind: Literal["service_task"] = KIND_SERVICE_TASK
    action: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class CallActivitySpec(FlowNode):
    """子流程调用：process_code 指向被调流程定义；数据映射 M5 完整实现。"""

    kind: Literal["call_activity"] = KIND_CALL_ACTIVITY
    process_code: str = ""
    # v2.3 A11（Zeebe bindingType 同义）：latest=运行期解析最新版（缺省）；
    # deployment=部署时必须已发布（ERROR）。
    binding: Literal["latest", "deployment"] = "latest"
    is_synchronous: bool = True
    input_mappings: dict[str, str] = Field(default_factory=dict)
    output_mappings: dict[str, str] = Field(default_factory=dict)


class CcTaskSpec(FlowNode):
    """传阅节点：recipients 每项为 CEL 表达式（字面人 'u_a' / initiator / 取人函数）。"""

    kind: Literal["cc_task"] = KIND_CC_TASK
    recipients: list[str] = Field(default_factory=list)
    # 传阅恒按 any 推进（对齐 对标产品 CIRCULATE 强制 ANY），多入边不等待
    advance_policy: Literal["any", "all"] | None = "any"


def register_builtin_nodes(registry: NodeRegistry) -> None:
    """把全部内置类型注册进注册表（与自定义类型同一入口）。

    ``ui`` 为设计器展示元数据：name 显示名 / category 调色板分组 /
    shape 画布形状 / color 主色 / palette 调色板预设项（含创建时的字段默认值）/
    form_groups 属性面板分组（title + fields，collapsed 默认折叠）。
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
            "form_groups": [{"title": "基础", "fields": ["name"]}],
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
            "form_groups": [{"title": "基础", "fields": ["name"]}],
        },
    )
    registry.register(
        KIND_BRANCH,
        BranchSpec,
        ui={
            "name": "条件分支",
            "category": "分支",
            "shape": "rect",
            "color": "#d97706",
            "icon": "branch",
            "palette": [
                {"label": "条件分支", "defaults": {"route_mode": "first_match"}},
                {"label": "条件分支(命中的都走)", "defaults": {"route_mode": "all_match"}},
            ],
            "form_groups": [{"title": "基础", "fields": ["name", "route_mode"]}],
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
            # 属性面板分组：常用在前，高级组默认折叠（渐进披露）
            "form_groups": [
                {"title": "基础", "fields": ["name"]},
                {
                    "title": "参与者",
                    "fields": [
                        "assignee",
                        "assignees",
                        "candidate_users",
                        "candidate_groups",
                        "no_participant_policy",
                    ],
                },
                {"title": "多人审批", "fields": ["multi_instance"], "collapsed": True},
                {
                    "title": "自动通过",
                    "fields": [
                        "auto_pass_originator",
                        "auto_pass_previous",
                        "auto_pass_participated",
                    ],
                    "collapsed": True,
                },
                {"title": "驳回与超时", "fields": ["reject_target", "timeout"], "collapsed": True},
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
            "form_groups": [
                {"title": "基础", "fields": ["name"]},
                {"title": "动作", "fields": ["action", "params"]},
            ],
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
            "form_groups": [
                {"title": "基础", "fields": ["name"]},
                {"title": "子流程", "fields": ["process_code", "is_synchronous"]},
                {
                    "title": "数据映射",
                    "fields": ["input_mappings", "output_mappings"],
                    "collapsed": True,
                },
            ],
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
            "form_groups": [
                {"title": "基础", "fields": ["name"]},
                {"title": "接收", "fields": ["recipients"]},
            ],
        },
    )
    register_wait_node(registry)
