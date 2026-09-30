"""网关行为：排他 / 并行 / 相容（含分支与汇聚语义）。

- EXCLUSIVE 排他：按出边顺序取第一个条件为真的边；全不中走默认边；再无则 RouteError。
- PARALLEL 并行：全部出边激活；汇聚侧等所有入边来源节点都有已完成令牌。
- INCLUSIVE 相容：条件为真的边全部激活；汇聚侧任一到达即通过，
  并取消其余未完成的兄弟分支（对齐 对标产品 或汇聚语义）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.definitions.builtin import GatewayKind, GatewaySpec
from flowork.engine.context import RuntimeNodeContext
from flowork.engine.kernel import RouteError
from flowork.expr.conditions import evaluate_condition
from flowork.runtime.behavior import NodeBehavior, NodeContext
from flowork.runtime.lifecycle import TokenState

if TYPE_CHECKING:
    from flowork.definitions.flow import SequenceFlow
    from flowork.persistence.orm import TokenRow

__all__ = ["GatewayBehavior"]


class GatewayBehavior(NodeBehavior):
    """网关节点行为：分支（get_outgoing）与汇聚（try_complete）语义。"""

    def _spec(self) -> GatewaySpec:
        spec = self.spec
        assert isinstance(spec, GatewaySpec)
        return spec

    # —— 分支 ——

    def get_outgoing(self, ctx: NodeContext) -> list[SequenceFlow]:
        assert isinstance(ctx, RuntimeNodeContext)
        definition = ctx.kernel.definition_of(ctx.session, ctx.instance)
        outgoing = definition.outgoing(ctx.node.code)
        has_conditions = any(flow.condition is not None for flow in outgoing)
        if not has_conditions:
            # 汇聚场景：出边全部无条件 → 全部直通
            return [flow for flow in outgoing if not flow.is_default]
        kind = self._spec().gateway_kind
        if kind == GatewayKind.EXCLUSIVE:
            return self._exclusive(outgoing, ctx)
        if kind == GatewayKind.INCLUSIVE:
            return self._inclusive(outgoing, ctx)
        return [flow for flow in outgoing if not flow.is_default]  # PARALLEL

    def _exclusive(
        self, outgoing: list[SequenceFlow], ctx: RuntimeNodeContext
    ) -> list[SequenceFlow]:
        for flow in outgoing:
            if flow.condition is not None and evaluate_condition(flow.condition, ctx.variables):
                return [flow]
        defaults = [flow for flow in outgoing if flow.is_default]
        if defaults:
            return defaults
        raise RouteError(f"排他网关 {ctx.node.code} 无匹配分支且未配置默认边")

    def _inclusive(
        self, outgoing: list[SequenceFlow], ctx: RuntimeNodeContext
    ) -> list[SequenceFlow]:
        matched = [
            flow
            for flow in outgoing
            if flow.condition is not None and evaluate_condition(flow.condition, ctx.variables)
        ]
        if matched:
            return matched
        defaults = [flow for flow in outgoing if flow.is_default]
        if defaults:
            return defaults
        raise RouteError(f"相容网关 {ctx.node.code} 无匹配分支且未配置默认边")

    # —— 汇聚 ——

    def try_complete(self, ctx: NodeContext) -> bool:
        assert isinstance(ctx, RuntimeNodeContext)
        kind = self._spec().gateway_kind
        if kind == GatewayKind.EXCLUSIVE:
            return True  # 排他汇聚：到达即过
        if kind == GatewayKind.INCLUSIVE:
            self._cancel_sibling_branches(ctx)
            self._cancel_parked_gateway_tokens(ctx)
            return True  # 或汇聚：任一到达即过
        satisfied = self._all_incoming_arrived(ctx)
        if satisfied:
            self._cancel_parked_gateway_tokens(ctx)  # 撤销其他已停驻的网关令牌
        return satisfied

    def _incoming_sources(self, ctx: RuntimeNodeContext) -> list[str]:
        definition = ctx.kernel.definition_of(ctx.session, ctx.instance)
        return [flow.source for flow in definition.incoming(ctx.node.code)]

    def _all_incoming_arrived(self, ctx: RuntimeNodeContext) -> bool:
        tokens = ctx.kernel.tokens(ctx.session, ctx.instance.id)
        finished_activities = {
            token.activity_code for token in tokens if token.state == TokenState.FINISHED.value
        }
        return all(source in finished_activities for source in self._incoming_sources(ctx))

    def _cancel_sibling_branches(self, ctx: RuntimeNodeContext) -> None:
        """或汇聚通过时取消仍挂起的入边来源令牌（兄弟分支取消）。"""
        assert ctx.token is not None
        sources = set(self._incoming_sources(ctx))
        unfinished = (TokenState.UNFINISHED.value,)
        for token in ctx.kernel.tokens(ctx.session, ctx.instance.id, states=unfinished):
            if token.id != ctx.token.id and token.activity_code in sources:
                ctx.kernel.cancel_token(ctx.session, token)

    def _cancel_parked_gateway_tokens(self, ctx: RuntimeNodeContext) -> None:
        """撤销同一网关上其他停驻令牌（避免重复路由）。"""
        assert ctx.token is not None
        unfinished = (TokenState.UNFINISHED.value,)
        for token in ctx.kernel.tokens(ctx.session, ctx.instance.id, states=unfinished):
            if token.activity_code == ctx.node.code and token.id != ctx.token.id:
                ctx.kernel.cancel_token(ctx.session, token)


def _unused(token: TokenRow) -> None:  # pragma: no cover
    """类型导入占位。"""
