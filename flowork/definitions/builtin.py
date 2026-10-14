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

from flowork.definitions.call_instance import CallInstanceConfig
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
    - ALL_MATCH 命中的都走：条件为真的边全部激活（行业原生语义）；皆空走默认边。
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
    """人工审批节点。四个取人字段每项为 CEL 表达式（spec §4.2/§5.5 位点统一）：
    字面人写 ``'u_a'``，发起人为保留标识符 ``initiator``，取人函数如
    ``manager_of(initiator)``（词汇经 EngineConfig.expression_functions 注册）。

    候选槽（candidate_users/candidate_groups）额外接受组与混合集：返回 ``groups``
    时**保留引用不展开**（认领时按目录判归属——明天入组的人能认领明天的待办），
    返回 ``mixed`` 时人落候选名单、组引用落名单（运行期同一列表混存）。
    """

    kind: Literal["user_task"] = KIND_USER_TASK
    assignee: str | None = None
    assignees: list[str] = Field(default_factory=list)
    candidate_users: list[str] = Field(
        default_factory=list,
        description="候选公式：如 'u_a' 或 users_with_role('finance')（求值出的人可认领）",
    )
    candidate_groups: list[str] = Field(
        default_factory=list,
        description="候选组公式：如 'role:finance' 或 'org:d_eng'（认领时按目录判归属）",
    )
    multi_instance: MultiInstance | None = None
    reject_target: str | None = None
    # —— 参与者策略：命中者自动通过（auto_pass 历史并计入同意数）——
    auto_pass_originator: bool = False
    auto_pass_previous: bool = False
    auto_pass_participated: bool = False
    no_participant_policy: str = "raise"  # raise / to_admins
    timeout: TimeoutPolicy | None = None
    # （spec §5.1）：超时动作列表（多级预警/改派/升级/自动通过·驳回），
    # 同节点上限 8 条（validator 把关）；旧 timeout 为其退化形态
    deadlines: list[TaskDeadline] = Field(default_factory=list)


class ServiceTaskSpec(FlowNode):
    """系统节点：执行 HandlerRegistry 中注册的动作。"""

    kind: Literal["service_task"] = KIND_SERVICE_TASK
    action: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class CallActivitySpec(FlowNode):
    """子流程调用：process_code 指向被调定义；数据映射 M5。"""

    kind: Literal["call_activity"] = KIND_CALL_ACTIVITY
    process_code: str = ""
    binding: Literal["latest", "deployment"] = "latest"  # deployment=部署时须已发布
    multi_instance: CallInstanceConfig | None = None  # 多实例（每人一子实例，index 聚合）
    is_synchronous: bool = True
    input_mappings: dict[str, str] = Field(default_factory=dict)
    output_mappings: dict[str, str] = Field(default_factory=dict)


class CcTaskSpec(FlowNode):
    """传阅节点：recipients 每项为 CEL 表达式（字面人 'u_a' / initiator / 取人函数）。"""

    kind: Literal["cc_task"] = KIND_CC_TASK
    recipients: list[str] = Field(default_factory=list)
    # 传阅恒按 any 推进（对齐传阅类强制 ANY 的行业惯例），多入边不等待
    advance_policy: Literal["any", "all"] | None = "any"


def register_builtin_nodes(registry: NodeRegistry) -> None:
    """把全部内置类型注册进注册表（与自定义类型同一入口）。

    ``ui`` 为设计器展示元数据，集中在 :mod:`flowork.definitions.builtin_ui`
    （纯数据模块，本文件守 300 行红线）；字段含义见该模块 docstring。
    """
    from flowork.definitions.builtin_ui import BUILTIN_UI

    for kind, spec_cls in (
        (KIND_START, StartSpec),
        (KIND_END, EndSpec),
        (KIND_BRANCH, BranchSpec),
        (KIND_USER_TASK, UserTaskSpec),
        (KIND_SERVICE_TASK, ServiceTaskSpec),
        (KIND_CALL_ACTIVITY, CallActivitySpec),
        (KIND_CC_TASK, CcTaskSpec),
    ):
        registry.register(kind, spec_cls, ui=BUILTIN_UI[kind])
    register_wait_node(registry)
