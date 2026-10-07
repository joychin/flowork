"""Python DSL 构建器：以代码定义流程，与 JSON 模板同构（单一事实源仍是 JSON）。

示例::

    definition = (
        FlowBuilder("leave", "请假审批")
        .start()
        .user_task("manager", name="经理审批", assignee="u_manager")
        .end()
        .connect("start", "manager")
        .connect("manager", "end")
        .build(registry)
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.definitions.builtin import KIND_BRANCH, RouteMode
from flowork.definitions.definition import DefinitionStatus, ProcessDefinition
from flowork.definitions.flow import FlowCondition, SequenceFlow
from flowork.definitions.node import FlowNode, MultiInstance

if TYPE_CHECKING:
    from flowork.runtime.registry import NodeRegistry

__all__ = ["FlowBuilder"]


class FlowBuilder:
    """流式构建器：先收集节点/连线，build() 时构造 ProcessDefinition。

    传入 registry 时节点解析为注册的 Spec 类（与 JSON 加载路径一致）；
    自定义节点用 ``.node(kind=..., **fields)`` 声明。
    """

    def __init__(self, code: str, name: str = "") -> None:
        self._code = code
        self._name = name
        self._nodes: list[dict[str, Any]] = []
        self._flows: list[SequenceFlow] = []

    # —— 节点 ——

    def start(self, code: str = "start", name: str = "开始", **fields: Any) -> FlowBuilder:
        return self._add_node(code, "start", name, fields)

    def end(self, code: str = "end", name: str = "结束", **fields: Any) -> FlowBuilder:
        return self._add_node(code, "end", name, fields)

    def user_task(
        self,
        code: str,
        name: str = "",
        *,
        assignee: str | None = None,
        assignees: list[str] | None = None,
        candidate_users: list[str] | None = None,
        candidate_groups: list[str] | None = None,
        multi_instance: MultiInstance | None = None,
        reject_target: str | None = None,
        **fields: Any,
    ) -> FlowBuilder:
        payload: dict[str, Any] = {
            "assignee": assignee,
            "assignees": assignees or [],
            "candidate_users": candidate_users or [],
            "candidate_groups": candidate_groups or [],
        }
        if multi_instance is not None:
            payload["multi_instance"] = multi_instance
        if reject_target is not None:
            payload["reject_target"] = reject_target
        payload.update(fields)
        return self._add_node(code, "user_task", name, payload)

    def service_task(
        self, code: str, name: str = "", *, action: str = "", **fields: Any
    ) -> FlowBuilder:
        return self._add_node(code, "service_task", name, {"action": action, **fields})

    def call_activity(
        self, code: str, name: str = "", *, process_code: str = "", **fields: Any
    ) -> FlowBuilder:
        return self._add_node(code, "call_activity", name, {"process_code": process_code, **fields})

    def cc_task(
        self, code: str, name: str = "", *, recipients: list[str] | None = None, **fields: Any
    ) -> FlowBuilder:
        return self._add_node(code, "cc_task", name, {"recipients": recipients or [], **fields})

    def branch(
        self,
        code: str,
        name: str = "",
        *,
        route_mode: RouteMode = RouteMode.FIRST_MATCH,
        **fields: Any,
    ) -> FlowBuilder:
        """条件分支节点：出边挂条件，route_mode 决定命中即走 / 命中的都走。

        多条入边节点的汇聚语义用 ``advance_policy``（any/all）经 ``**fields`` 配置。
        """
        return self._add_node(code, KIND_BRANCH, name, {"route_mode": route_mode, **fields})

    def node(self, code: str, *, kind: str, name: str = "", **fields: Any) -> FlowBuilder:
        """自定义节点入口：kind 必须已在注册表注册。"""
        if not kind:
            raise ValueError("自定义节点必须提供 kind")
        return self._add_node(code, kind, name, fields)

    # —— 连线 ——

    def flow(
        self,
        source: str,
        target: str,
        *,
        name: str = "",
        condition: FlowCondition | None = None,
        default: bool = False,
        **fields: Any,
    ) -> FlowBuilder:
        self._flows.append(
            SequenceFlow(
                source=source,
                target=target,
                name=name,
                is_default=default,
                condition=condition,
                **fields,
            )
        )
        return self

    def connect(self, source: str, target: str) -> FlowBuilder:
        """无条件连边的便捷别名。"""
        return self.flow(source, target)

    # —— 构造 ——

    def build(
        self,
        registry: NodeRegistry | None = None,
        *,
        major: int = 1,
        revision: int = 1,
        status: DefinitionStatus = DefinitionStatus.PUBLISHED,
    ) -> ProcessDefinition:
        nodes: list[FlowNode] = []
        for raw in self._nodes:
            spec_cls = registry.spec_cls(raw["kind"]) if registry is not None else FlowNode
            nodes.append(spec_cls.model_validate(raw))
        return ProcessDefinition(
            code=self._code,
            name=self._name,
            major=major,
            revision=revision,
            status=status,
            nodes=nodes,
            flows=list(self._flows),
        )

    def _add_node(self, code: str, kind: str, name: str, fields: dict[str, Any]) -> FlowBuilder:
        payload = {k: v for k, v in fields.items() if v not in (None, [], {})}
        self._nodes.append({"code": code, "name": name, "kind": kind, **payload})
        return self
