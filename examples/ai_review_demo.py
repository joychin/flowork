"""ai_review 自定义节点 demo（M0 验收项）：证明"加一种节点类型不改引擎核心"。

演示链路（引擎外注册 → 定义 → 校验 → 行为 → 路由判定）：
1. ``AiReviewSpec`` / ``AiReviewBehavior`` 完全定义在引擎包之外；
2. 经 ``NodeRegistry.register`` 注册后，模板即可引用 ``ai_review`` 节点（含校验与序列化）；
3. 行为 ``on_enter`` 调用 mock 模型，产出结构化输出写入变量；
4. 条件分支（命中即走）按置信度路由：高置信 → 直接通过；低置信 → 升级人工审批（HITL 模式）。

运行：``python examples/ai_review_demo.py``
"""

from __future__ import annotations

from typing import Any

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition
from flowork.definitions.flow import FlowCondition
from flowork.definitions.node import FlowNode
from flowork.definitions.validator import IssueLevel, validate_definition
from flowork.expr.conditions import evaluate_condition
from flowork.runtime.behavior import InMemoryNodeContext, NodeBehavior
from flowork.runtime.registry import NodeRegistry, create_registry

AI_REVIEW_KIND = "ai_review"


class AiReviewSpec(FlowNode):
    """自定义节点定义：声明本类型专有字段（获得校验与序列化）。"""

    kind: str = AI_REVIEW_KIND
    prompt: str = ""
    model_name: str = "mock-reviewer-v1"
    confidence_threshold: float = 0.8


class AiReviewBehavior(NodeBehavior):
    """自定义节点行为：mock 模型调用 → 结构化输出写入变量。

    真实 LLM 适配在二期 ``flowork-ai-behaviors``（本 demo 不引入任何外部依赖）。
    """

    def on_enter(self, ctx: InMemoryNodeContext) -> None:
        spec = self.spec
        assert isinstance(spec, AiReviewSpec)
        verdict = _mock_llm(spec.prompt, ctx.variables)
        ctx.variables.update(verdict)
        ctx.emit("ai_review_finished", {"node": spec.code, "confidence": verdict["ai_confidence"]})
        ctx.info(f"模型 {spec.model_name} 完成 review，置信度 {verdict['ai_confidence']}")


def _mock_llm(prompt: str, variables: dict[str, Any]) -> dict[str, Any]:
    """确定性 mock：金额越大越不确定，便于演示两条路由。"""
    amount = float(variables.get("amount", 0))
    if amount > 10_000:
        confidence, recommendation = 0.42, "need_human"
    else:
        confidence, recommendation = 0.93, "approve"
    return {
        "ai_confidence": confidence,
        "ai_recommendation": recommendation,
        "ai_prompt": prompt,
    }


def build_definition(registry: NodeRegistry | None = None) -> ProcessDefinition:
    """构建含 ai_review 节点的模板（经 builder，与 JSON 同构）。"""
    high_confidence = FlowCondition(
        groups=[[{"left": "ai_confidence", "operator": "ge", "right": 0.8}]]
    )
    return (
        FlowBuilder("ai_review_flow", "AI 预审 + 人工升级")
        .start()
        .node(
            "review",
            kind=AI_REVIEW_KIND,
            name="AI 预审",
            prompt="评估该申请的合规风险",
            confidence_threshold=0.8,
        )
        .branch("route", name="按置信度分流")
        .user_task("human_review", name="人工复核（升级）", assignee="u_specialist")
        .end("done", name="完成")
        .connect("start", "review")
        .connect("review", "route")
        .flow("route", "done", condition=high_confidence)
        .flow("route", "human_review", default=True)
        .connect("human_review", "done")
        .build(registry)
    )


def run_scenario(registry: NodeRegistry, variables: dict[str, Any]) -> dict[str, Any]:
    """执行一个场景：行为产出变量 → 分支条件求值 → 返回路由结果。"""
    definition = build_definition(registry)
    review_node = definition.node("review")
    assert review_node is not None
    behavior_cls = registry.behavior_cls(AI_REVIEW_KIND)
    assert behavior_cls is not None
    behavior = behavior_cls(review_node)
    ctx = InMemoryNodeContext(review_node, variables)
    behavior.on_enter(ctx)

    outgoing = definition.outgoing("route")
    matched = [
        flow
        for flow in outgoing
        if flow.condition is not None and evaluate_condition(flow.condition, ctx.variables)
    ]
    if matched:  # 命中即走：条件边命中则不走默认边
        routed = [flow.target for flow in matched]
    else:
        routed = [f"{flow.target} (default)" for flow in outgoing if flow.is_default]
    return {
        "variables": ctx.variables,
        "events": [name for name, _ in ctx.events],
        "route_to": routed,
    }


def run_demo() -> list[dict[str, Any]]:
    """注册自定义类型（引擎外）并执行低/高置信两个场景。"""
    registry = create_registry()
    registry.register(AI_REVIEW_KIND, AiReviewSpec, AiReviewBehavior)

    definition = build_definition()
    issues = validate_definition(definition, registry)
    errors = [i for i in issues if i.level == IssueLevel.ERROR]
    if errors:
        raise SystemExit(f"校验未通过：{errors}")

    low = run_scenario(registry, {"amount": 12_000})
    high = run_scenario(registry, {"amount": 800})
    assert any("human_review" in r for r in low["route_to"]), "低置信度必须升级人工"
    assert "done" in high["route_to"], "高置信度应直接通过"
    return [low, high]


if __name__ == "__main__":
    labels = ["低置信度场景(amount=12000)", "高置信度场景(amount=800)"]
    for label, result in zip(labels, run_demo(), strict=True):
        print(f"== {label} ==")
        print(
            f"  变量: amount={result['variables']['amount']}, "
            f"ai_confidence={result['variables']['ai_confidence']}, "
            f"ai_recommendation={result['variables']['ai_recommendation']}"
        )
        print(f"  事件: {result['events']}")
        print(f"  路由: {result['route_to']}")
