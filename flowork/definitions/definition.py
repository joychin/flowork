"""ProcessDefinition：流程定义（版本化，发布即不可变）+ JSON 编解码。

加载路径 ``load_definition`` 经 NodeRegistry 把每个节点解析为注册的 Spec 类，
未知 kind 抛 UnknownNodeKind（设计期拦截，G2）。
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

from flowork.definitions.flow import SequenceFlow
from flowork.definitions.node import FlowNode
from flowork.errors import DefinitionError
from flowork.runtime.registry import NodeRegistry

__all__ = [
    "DeadlineStrategy",
    "DefinitionStatus",
    "InstanceDeadline",
    "InvalidDefinitionData",
    "NodePosition",
    "ProcessDefinition",
    "ProcessVariable",
    "dump_definition",
    "load_definition",
    "load_definition_file",
]


class DefinitionStatus(StrEnum):
    """v2（spec §6.2）：draft 取消——deploy 即 publish；retired 不接新实例。"""

    PUBLISHED = "published"
    RETIRED = "retired"


class NodePosition(BaseModel):
    """设计器画布坐标（纯展示数据，引擎不消费；semantic diff 应忽略）。"""

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


class ProcessVariable(BaseModel):
    """发起时的流程变量声明（设计器级元数据）。

    用途：设计器变量选择器的枚举来源、dry-run 取样默认值、
    校验器对条件/公式未知变量引用的预警。运行时变量以实际 payload 为准，
    声明不约束传参（多余或缺失的变量由引擎按条件求值行为处理）。
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["string", "number", "boolean", "list"] = "string"
    label: str = ""
    # dry-run 取样默认值；类型与 type 一致（list 为任意 JSON 数组）
    sample: Any = None
    # 可检索（v2 R2/P1-5）：仅 string/number/boolean 可标（校验器把关），
    # True 时变量值同步进 wf_variable_index
    searchable: bool = False


class DeadlineStrategy(StrEnum):
    """实例时限策略（v2.2 spec §7）：Temporal Workflow Execution Timeout 同款语义。"""

    MARK = "mark"
    CANCEL = "cancel"
    TERMINATE = "terminate"


class InstanceDeadline(BaseModel):
    """实例级时限：整单最大存活时间（单对象，非列表；到点按 strategy 处置）。"""

    model_config = ConfigDict(extra="forbid")

    after_minutes: int = Field(gt=0)
    strategy: DeadlineStrategy = DeadlineStrategy.MARK


class InvalidDefinitionData(DefinitionError):
    """定义数据不是合法的 JSON 对象。"""


class ProcessDefinition(BaseModel):
    """流程定义：nodes + flows 构成的有向图（JSON 单一事实源）。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    name: str = ""
    # v2（spec §6）：版本＝code + major + revision；同 code 可对老 major 续发
    # revision（维护分支）。deploy 时由版本判定覆写，模型默认值仅占位。
    major: int = 1
    revision: int = 1
    status: DefinitionStatus = DefinitionStatus.PUBLISHED
    # v2（spec §3.2）：input 契约（JSON Schema 2020-12）。None＝无契约；
    # 有值时发起/任务提交/节点输出三处边界按 schema 校验（P0-6）。
    input: dict[str, Any] | None = None
    # v2.2（spec §7）：实例级时限——发起时按 after_minutes 物化 deadline_at，
    # 到点 mark（打标继续跑）/cancel（作废）/terminate（终止）
    deadline: InstanceDeadline | None = None
    # SerializeAsAny：节点按实例类型（Spec 子类）序列化，否则子类字段会在 dump 时丢失
    nodes: list[SerializeAsAny[FlowNode]] = Field(default_factory=list)
    flows: list[SequenceFlow] = Field(default_factory=list)
    # 流程变量声明（设计器元数据；见 ProcessVariable）
    variables: list[ProcessVariable] = Field(default_factory=list)
    # 设计器画布坐标（activity_code → {x,y}）；缺失时设计器自动布局
    layout: dict[str, NodePosition] = Field(default_factory=dict)

    def node(self, code: str) -> FlowNode | None:
        return next((n for n in self.nodes if n.code == code), None)

    def outgoing(self, code: str) -> list[SequenceFlow]:
        return [f for f in self.flows if f.source == code]

    def incoming(self, code: str) -> list[SequenceFlow]:
        return [f for f in self.flows if f.target == code]

    def start_node(self) -> FlowNode | None:
        return next((n for n in self.nodes if n.kind == "start"), None)


def dump_definition(definition: ProcessDefinition) -> dict[str, Any]:
    """序列化为可 JSON 化的 dict（与模板文件同构）。"""
    return definition.model_dump(mode="json")


def _resolve_node(raw: dict[str, Any], registry: NodeRegistry) -> FlowNode:
    kind = str(raw.get("kind", ""))
    spec_cls = registry.spec_cls(kind)
    return spec_cls.model_validate(raw)


def load_definition(data: dict[str, Any] | str, registry: NodeRegistry) -> ProcessDefinition:
    """从 dict 或 JSON 字符串加载定义：节点经注册表解析为对应 Spec 类。

    旧版兼容（v2 §6.2 迁移）：``version`` 字段忽略；``status: draft`` 视同
    published（draft 已取消，编辑态在 git/设计器本地）。
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:  # pragma: no cover - 简单包装
            raise InvalidDefinitionData(f"JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise InvalidDefinitionData("定义数据必须是 JSON 对象")
    data = dict(data)
    data.pop("version", None)
    if data.get("status") == "draft":
        data["status"] = DefinitionStatus.PUBLISHED.value
    raw_nodes = data.get("nodes", [])
    if not isinstance(raw_nodes, list):
        raise InvalidDefinitionData("nodes 必须是数组")
    resolved_nodes = [_resolve_node(raw, registry) for raw in raw_nodes]
    return ProcessDefinition.model_validate({**data, "nodes": resolved_nodes})


def load_definition_file(path: str | Path, registry: NodeRegistry) -> ProcessDefinition:
    """从模板文件加载定义。"""
    text = Path(path).read_text(encoding="utf-8")
    return load_definition(text, registry)
