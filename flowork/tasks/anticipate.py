"""anticipate 只读预判（v2.2 spec §11/P1-9）：AI 原生差异点 API。

独立模块（TaskService 经 mixin 组合，engine.task.anticipate 入口不变）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.engine.ops import CommandContext
from flowork.errors import TaskNotAvailableError
from flowork.tasks.models import AnticipateResult

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["AnticipateMixin"]


class AnticipateMixin:
    """TaskService 的只读预判面（组合于 TaskOpsMixin 之上）。"""

    _engine: ProcessEngine

    def _instance(self, ctx: CommandContext, instance_id: str) -> Any:
        """由宿主类提供（TaskOpsMixin._instance）。"""
        raise NotImplementedError

    # —— 只读预判（v2.2 spec §11/P1-9）——

    def anticipate(self, task_id: str, outcome: str = "agree") -> AnticipateResult:
        """预判「照此提交后节点是否出、实例是否完」——纯读，不落库不改状态。

        主流引擎无等价 API（AI 原生差异点）：UI 的「你是最后一个审批人」提示、
        AI 预审共用。读令牌会签计数＋出口规则即时计算；instance_complete 以当前
        变量值沿路由近似（提交 output 可能改写条件分支，结果按此解读）。
        """
        result: AnticipateResult = self._engine.execute(
            lambda ctx: self._anticipate(ctx, task_id, outcome)
        )
        return result

    def _anticipate(self, ctx: CommandContext, task_id: str, outcome: str) -> AnticipateResult:
        from flowork.definitions.builtin import CallActivitySpec, EndSpec, UserTaskSpec
        from flowork.engine.routing import evaluation_variables, select_outgoing
        from flowork.expr.context import expression_functions_of
        from flowork.persistence.orm import TaskRow as _TaskRow
        from flowork.persistence.orm import TokenRow as _TokenRow
        from flowork.tasks.countersign import exit_reached

        task = ctx.session.get(_TaskRow, task_id)
        if task is None or task.state != "ready":
            raise TaskNotAvailableError(f"任务不可预判（不存在或已处理）：{task_id}")
        instance = self._instance(ctx, task.instance_id)
        token = ctx.session.get(_TokenRow, task.token_id)
        assert token is not None
        kernel = self._engine.kernel_for(ctx)
        definition = kernel.definition_of(ctx.session, instance)
        node = definition.node(task.activity_code)
        assert node is not None
        spec = node if isinstance(node, UserTaskSpec) else None
        mi = spec.multi_instance if spec else None
        variables = evaluation_variables(instance, instance.variables)
        functions = expression_functions_of(self._engine.config)

        def _exit(expr: str | None, *, approve: int, disapprove: int, side: str) -> bool:
            """假想计数下的出口判定（缺省全票语义下沉在 exit_reached）。"""
            return exit_reached(
                expr,
                side=side,  # type: ignore[arg-type]
                item_count=token.item_count,
                approve_count=approve,
                disapprove_count=disapprove,
                variables=variables,
                start_user=instance.start_user,
                functions=functions,
            )

        if outcome == "reject":
            # 假想 +1 票否决：非会签节点任一否决即出口（原 >=1 语义）；会签未配＝全员否决
            expr = mi.disapprove_exit if mi is not None else "task.disapprove_count >= 1"
            node_exit = _exit(
                expr,
                approve=token.approve_count,
                disapprove=token.disapprove_count + 1,
                side="disapprove",
            )
        elif outcome == "agree":
            node_exit = _exit(
                mi.approve_exit if mi else None,
                approve=token.approve_count + 1,
                disapprove=token.disapprove_count,
                side="approve",
            )
        else:
            raise ValueError(f"假想 outcome 仅支持 agree/reject：{outcome!r}")

        if not node_exit:
            return AnticipateResult(node_exit=False, instance_complete=False)

        # 节点将出：按当前变量近似路由，看是否无需再等任何人工/子流程即到 end
        if outcome == "reject" and spec is not None:
            reject_target = spec.reject_target or token.source_activity_code or ""
            targets = [reject_target] if reject_target else []
        else:
            targets = [
                flow.target
                for flow in select_outgoing(
                    definition.outgoing(node.code),
                    variables,
                    mode=getattr(node, "route_mode", "all_match"),
                    functions=functions,
                )
            ]
        waiting_specs = (UserTaskSpec, CallActivitySpec)
        complete = True
        seen: set[str] = set()
        frontier = list(targets)
        while frontier and complete:
            code = frontier.pop()
            if code in seen:
                continue
            seen.add(code)
            hop = definition.node(code)
            if hop is None:
                complete = False
                break
            if isinstance(hop, EndSpec):
                continue
            if isinstance(hop, waiting_specs):
                complete = False
                break
            frontier.extend(
                flow.target
                for flow in select_outgoing(
                    definition.outgoing(hop.code), variables, mode="all_match", functions=functions
                )
            )
            if len(seen) > 200:  # 环路守卫
                complete = False
        return AnticipateResult(node_exit=True, instance_complete=complete)
