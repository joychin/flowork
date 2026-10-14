"""推进门（kernel 守 300 行红线下沉件）：边到达后的激活判定。

any 任一到达即办（缺省）/ all 可达性放行；any 放行的竞速收尾
推迟到 route 完结段。语义权威与调用方见 kernel.route。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.engine.context import RuntimeNodeContext
from flowork.engine.declared_hooks import run_declared_hook
from flowork.engine.errors import RouteError
from flowork.engine.join_gate import should_release
from flowork.runtime.lifecycle import TokenState

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.definition import ProcessDefinition
    from flowork.definitions.flow import SequenceFlow
    from flowork.definitions.node import FlowNode
    from flowork.persistence.orm import ProcessInstanceRow, TokenRow

__all__ = ["AdvanceGateMixin"]


class AdvanceGateMixin:
    """边到达→停靠判定；宿主 Kernel 提供 tokens/activate/cancel_racing_arms。"""

    if TYPE_CHECKING:
        # 宿主 Kernel 提供（Mixin 组合，同 task_records/token_records 的宿主声明惯例）
        def activate(
            self,
            session: Session,
            instance: ProcessInstanceRow,
            node: FlowNode,
            *,
            source_activity_code: str | None = None,
            reject_flag: bool = False,
            manual_participants: dict[str, list[str]] | None = None,
            defer_completion: bool = False,
        ) -> TokenRow: ...

        def tokens(
            self, session: Session, instance_id: str, states: tuple[str, ...] | None = None
        ) -> list[TokenRow]: ...

        def cancel_racing_arms(
            self,
            session: Session,
            instance: ProcessInstanceRow,
            definition: ProcessDefinition,
            code: str,
        ) -> None: ...

    def _take_flow(
        self,
        session: Session,
        instance: ProcessInstanceRow,
        flow: SequenceFlow,
        from_token: TokenRow,
        definition: ProcessDefinition,
    ) -> tuple[FlowNode, TokenRow, bool] | None:
        if flow.on_take is not None:
            from typing import cast

            from flowork.engine.kernel import Kernel

            source_node = definition.node(flow.source)
            assert source_node is not None
            # 统一咽喉 declared_hooks：模拟跳过 + 未注册/异常＝技术故障
            run_declared_hook(
                RuntimeNodeContext(
                    cast("Kernel", self), session, instance, source_node, from_token
                ),
                flow.on_take,
            )
        target = definition.node(flow.target)
        if target is None:
            raise RouteError(f"目标节点不存在：{flow.target}")
        return self._advance(session, instance, target, flow, definition)

    def _advance(
        self,
        session: Session,
        instance: ProcessInstanceRow,
        node: FlowNode,
        flow: SequenceFlow,
        definition: ProcessDefinition,
    ) -> tuple[FlowNode, TokenRow, bool] | None:
        """边到达后的推进判定：any 任一到达即办（缺省）/ all 可达性放行。

        - all：除已停靠本节点的令牌外无任何**未完令牌**可结构可达本节点才放行
          （条件未命中的臂落定后不再阻塞；旧 settled 令牌不算数，见 join_gate）；
        - any 且节点已在办：吸收本次到达，不重复激活；
        - any 放行：竞速收尾**推迟到 route 完结段**（返回第三元标记）——扇出
          多边时靠后边的取消须等靠前边深递归落定后再跑，才收得掉回环结构
          （如 suite_route_mix 的双重默认臂）在递归中产生的竞速臂令牌。
        驳回/撤回/管理员等直调 ``activate`` 的路径不经此门。
        """
        sources = [f.source for f in definition.incoming(node.code)]
        racing_cancel = False
        if len(sources) > 1:
            if self._advance_policy_of(node) == "all":
                unfinished_elsewhere = {
                    token.activity_code
                    for token in self.tokens(
                        session, instance.id, states=(TokenState.UNFINISHED.value,)
                    )
                    if token.activity_code != node.code
                }
                if not should_release(definition, unfinished_elsewhere, node.code):
                    return None  # 仍有未完令牌可达，等待其余入边落定
            else:
                busy = any(
                    token.activity_code == node.code
                    for token in self.tokens(
                        session, instance.id, states=(TokenState.UNFINISHED.value,)
                    )
                )
                if busy:
                    return None  # 吸收重复到达（节点已在办）
                racing_cancel = True
        token = self.activate(
            session, instance, node, source_activity_code=flow.source, defer_completion=True
        )
        return node, token, racing_cancel

    def _advance_policy_of(self, node: FlowNode) -> str:
        """节点开始条件（Spec 声明；cc_task 在其 Spec 上覆写恒 any）。缺省 any。"""
        return getattr(node, "advance_policy", None) or "any"
