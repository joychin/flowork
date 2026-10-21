"""故障修复动作 skip 的手术实现。

跳过**故障节点**（incident.activity_code 圈定范围；2026-10-21 内核 review P0-1
收口——修前对全部未完令牌生效，并行分支上的人工待办会被静默取消放行）：

- 故障节点有未完令牌（激活后失败）：只取消该节点的令牌，沿其出边推进；
- 无持久令牌（激活期整笔回滚，令牌停在前驱）：取消指入故障节点的前驱令牌
  （其待办随 cancel_token 咽喉归档，by=incident:skip＝管理员越权处置的留痕），
  前驱按 all_match 正常路由但**跳过指向故障节点的边**，再补上故障节点自己的
  后继——故障节点绝不重新执行（skip 就是跳过），兄弟分支照常激活（all 汇聚
  不丢臂）；
- 故障码不在本实例定义内（子流程/MI 内故障帧，故障单挂父实例）：失败命令
  整体回滚、父侧令牌停在进入子流程的前一跳。恰一条未完令牌＝无歧义手术点，
  从该节点 all_match 重路由（重入调用＝新建子实例，不是重新执行故障节点）；
  多条未完令牌无法判定砍哪条，拒绝；
- 无节点定位（非节点作用域故障）：拒绝，指路 move_token/retry。

事件面：node_cancelled/task_cancelled 由 cancel_token 咽喉统一发（by=incident:skip）；
收尾置回 running 时补 process_resumed（挂起/恢复对称）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.errors import FloworkError
from flowork.events.domain import DomainEvent
from flowork.expr.context import expression_functions_of
from flowork.runtime.lifecycle import TERMINAL_INSTANCE_STATES, InstanceState, TokenState

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.engine.kernel import Kernel
    from flowork.engine.ops import CommandContext
    from flowork.persistence.orm import ProcessInstanceRow, TokenRow

__all__ = ["skip_failed_nodes"]

# (路由起点节点, 候选出边) —— 一跳或两跳（前驱余边 + 故障节点后继）
_Hops = list[tuple[str, list[Any]]]


def skip_failed_nodes(
    engine: ProcessEngine,
    kernel: Kernel,
    ctx: CommandContext,
    instance_id: str,
    actor: str,
    *,
    activity_code: str | None,
) -> None:
    """跳过故障节点（incident resolve 动作之一；同事务内联执行）。"""
    from flowork.engine.route_audit import RoutingAudit, persist_route_decision
    from flowork.engine.routing import evaluation_variables, select_outgoing
    from flowork.persistence.orm import ProcessInstanceRow
    from flowork.tasks.facts import attach_node

    instance: ProcessInstanceRow | None = ctx.session.get(ProcessInstanceRow, instance_id)
    assert instance is not None
    if instance.state in TERMINAL_INSTANCE_STATES:
        # 终态守卫：skip 会激活后继节点——
        # 对已终态实例执行会造出僵尸令牌（与 resume 守卫同一道防线）
        raise FloworkError(f"实例 {instance_id} 已终态（{instance.state}），不能跳过节点")
    if not activity_code:
        raise FloworkError(
            "该故障无节点定位（非节点作用域），skip 不适用——请修复后 retry，"
            "或用 move_token 做令牌手术"
        )
    definition = kernel.definition_of(ctx.session, instance)
    tokens = kernel.tokens(ctx.session, instance_id, states=(TokenState.UNFINISHED.value,))
    failed = definition.node(activity_code)
    plans: list[tuple[TokenRow, _Hops]] = []

    if failed is not None:
        at_failed = [t for t in tokens if t.activity_code == failed.code]
        direct_sources = {f.source for f in definition.flows if f.target == failed.code}
        direct = [t for t in tokens if t.activity_code in direct_sources]
        if at_failed:
            # 激活后失败：砍故障节点自己的令牌，从它出边推进（绝不重执行）
            plans = [(t, [(failed.code, definition.outgoing(failed.code))]) for t in at_failed]
        elif direct:
            # 激活期回滚、令牌停在前驱：砍前驱令牌，前驱路由跳过指向故障节点的
            # 边，再补故障节点自己的后继——故障节点不重新执行，兄弟分支照常激活
            for t in direct:
                surviving = [
                    f for f in definition.outgoing(t.activity_code) if f.target != failed.code
                ]
                plans.append(
                    (
                        t,
                        [
                            (t.activity_code, surviving),
                            (failed.code, definition.outgoing(failed.code)),
                        ],
                    )
                )
        else:
            # 回滚穿过瞬时节点（branch 等），令牌停在更上游：恰一条祖先令牌＝
            # 无歧义手术点，从该节点 all_match 重放路由（可能经行故障节点——
            # 激活期回滚＝故障节点从未真正开始，修复后经行是正解；未修复则
            # skip 自身失败，请改 retry）；多条祖先令牌无法判定砍哪条，拒绝
            from flowork.definitions.graph import upstream_basin

            basin = upstream_basin(definition, failed.code)
            ancestors = [t for t in tokens if t.activity_code in basin]
            if len(ancestors) == 1 and definition.node(ancestors[0].activity_code) is not None:
                only = ancestors[0]
                plans.append(
                    (only, [(only.activity_code, definition.outgoing(only.activity_code))])
                )
            elif len(ancestors) > 1:
                raise FloworkError(
                    f"故障节点 {failed.code} 上游有 {len(ancestors)} 条未完令牌，"
                    "无法判定手术点——请修复后 retry，或用 move_token 逐条手术"
                )
            else:
                raise FloworkError(
                    f"无法定位故障节点 {failed.code} 的令牌（节点前后均无未完令牌），无法跳过"
                )
    elif len(tokens) == 1 and definition.node(tokens[0].activity_code) is not None:
        # 子内/MI 内故障帧（节点住子定义，故障单挂父）：单令牌＝无歧义，
        # 从该节点重路由（重入调用＝新建子实例）
        only = tokens[0]
        plans.append((only, [(only.activity_code, definition.outgoing(only.activity_code))]))
    else:
        raise FloworkError(
            f"故障节点 {activity_code} 不在本实例定义内（子流程内故障），"
            f"且实例有 {len(tokens)} 条未完令牌无法判定手术点——请修复后 retry，"
            "或用 move_token 逐条手术"
        )

    base_functions = expression_functions_of(engine.config)
    for token, hops in plans:
        # node_cancelled/task_cancelled 由 cancel_token 咽喉统一发（by=incident:skip）
        # ——此处不再手发防双发
        kernel.cancel_token(ctx.session, token, cause="incident:skip", actor=actor)
        audit = RoutingAudit()
        variables = evaluation_variables(
            instance, instance.variables, session=ctx.session, now=engine.config.now()
        )
        functions = attach_node(
            base_functions, ctx.session, instance.id, node_calls=audit.node_calls
        )
        for source_code, edges in hops:
            for flow in select_outgoing(
                edges, variables, mode="all_match", functions=functions, audit=audit
            ):
                target: Any = definition.node(flow.target)
                if target is not None:
                    kernel.activate(ctx.session, instance, target, source_activity_code=source_code)
        # 手术行 source 钉在被砍令牌所在节点（多段路由同审计累计择道）
        audit.source = token.activity_code
        persist_route_decision(ctx.session, instance_id, token.id, audit)
    if instance.state == InstanceState.SUSPENDED.value:
        instance.state = InstanceState.RUNNING.value
        kernel.record_event(
            DomainEvent.PROCESS_RESUMED,
            {"actor": actor, "reason": "incident:skip", "deferred_minutes": None},
            instance_id,
        )
    ctx.touch(instance)
