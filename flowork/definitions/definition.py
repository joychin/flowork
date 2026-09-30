"""ProcessDefinition：流程定义（版本化，发布即不可变）+ JSON 编解码。

加载路径 ``load_definition`` 经 NodeRegistry 把每个节点解析为注册的 Spec 类，
未知 kind 抛 UnknownNodeKind（设计期拦截，G2）。
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

from flowork.definitions.flow import SequenceFlow
from flowork.definitions.node import FlowNode
from flowork.errors import DefinitionError
from flowork.runtime.registry import NodeRegistry

__all__ = [
    "DefinitionStatus",
    "InvalidDefinitionData",
    "ProcessDefinition",
    "dump_definition",
    "load_definition",
    "load_definition_file",
]


class DefinitionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class InvalidDefinitionData(DefinitionError):
    """定义数据不是合法的 JSON 对象。"""


class ProcessDefinition(BaseModel):
    """流程定义：nodes + flows 构成的有向图（JSON 单一事实源）。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    name: str = ""
    version: int = 1
    status: DefinitionStatus = DefinitionStatus.DRAFT
    # SerializeAsAny：节点按实例类型（Spec 子类）序列化，否则子类字段会在 dump 时丢失
    nodes: list[SerializeAsAny[FlowNode]] = Field(default_factory=list)
    flows: list[SequenceFlow] = Field(default_factory=list)

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
    """从 dict 或 JSON 字符串加载定义：节点经注册表解析为对应 Spec 类。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:  # pragma: no cover - 简单包装
            raise InvalidDefinitionData(f"JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise InvalidDefinitionData("定义数据必须是 JSON 对象")
    raw_nodes = data.get("nodes", [])
    if not isinstance(raw_nodes, list):
        raise InvalidDefinitionData("nodes 必须是数组")
    resolved_nodes = [_resolve_node(raw, registry) for raw in raw_nodes]
    return ProcessDefinition.model_validate({**data, "nodes": resolved_nodes})


def load_definition_file(path: str | Path, registry: NodeRegistry) -> ProcessDefinition:
    """从模板文件加载定义。"""
    text = Path(path).read_text(encoding="utf-8")
    return load_definition(text, registry)
