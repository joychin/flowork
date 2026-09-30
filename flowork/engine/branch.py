"""分支行为：条件路由（纯路由锚点，无副作用）。

- FIRST_MATCH 命中即走：按出边顺序取第一条条件为真的边；全不中走默认边；再无则 RouteError。
- ALL_MATCH 命中的都走：无条件边全部激活 + 条件为真者激活；皆空走默认边
  （对标产品 原生语义：命中几条走几条）。

汇聚语义不在本类：由 kernel 推进门按目标节点 ``advance_policy``（any/all）决定
（对齐 对标产品 preCondition ALL/ANY + handleActiviteActivity 兄弟取消）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.definitions.builtin import BranchSpec, RouteMode
from flowork.engine.context import RuntimeNodeContext
from flowork.runtime.behavior import NodeBehavior, NodeContext

if TYPE_CHECKING:
    from flowork.definitions.flow import SequenceFlow

__all__ = ["BranchBehavior"]


class BranchBehavior(NodeBehavior):
    """条件分支行为：仅覆写分支侧（get_outgoing）；进入即完成（基类默认）。"""

    def _spec(self) -> BranchSpec:
        spec = self.spec
        assert isinstance(spec, BranchSpec)
        return spec

    def get_outgoing(self, ctx: NodeContext) -> list[SequenceFlow]:
        assert isinstance(ctx, RuntimeNodeContext)
        definition = ctx.kernel.definition_of(ctx.session, ctx.instance)
        outgoing = definition.outgoing(ctx.node.code)
        from flowork.engine.routing import select_outgoing

        mode = "first_match" if self._spec().route_mode == RouteMode.FIRST_MATCH else "all_match"
        return select_outgoing(outgoing, dict(ctx.variables), mode=mode)
