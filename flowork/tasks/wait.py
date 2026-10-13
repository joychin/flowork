"""等待节点行为（spec §7.2 / wait-node.md §3）：等消息 / 等时长 / 两者竞速。

完成只经 :func:`finish_wait` 外部触发（消息命中 / 到时 / 广播唤醒三路统一），
``try_complete`` 恒 False；等待结果快照经 ``behavior_override`` 随路由注入
（wait.* 仅出边条件可用，不落实例变量）。竞速仲裁零新代码——命中与到时
各经 touch 乐观锁串行，后到者查令牌已完结即 no-op。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from flowork.definitions.flow import SequenceFlow
from flowork.definitions.wait_node import WaitSpec
from flowork.engine.context import RuntimeNodeContext
from flowork.engine.kernel import EngineExecutionError
from flowork.expr.cel import FormulaError, evaluate_formula_value
from flowork.runtime.behavior import NodeBehavior, NodeContext

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.engine.kernel import Kernel
    from flowork.persistence.orm import ProcessInstanceRow, TokenRow

__all__ = ["WaitBehavior", "finish_wait", "schedule_wait_job"]


class WaitBehavior(NodeBehavior):
    """等待节点：on_enter 登记订阅与到时 Job；完成由外部触发收口 finish_wait。"""

    # 等待解决事实（finish_wait 注入、get_outgoing 消费：result/message_name/message_id）
    resolution: dict[str, Any] | None = None
    advances_on_message = True  # 消息命中即推进（NodeBehavior 类级标志）

    def on_enter(self, ctx: NodeContext) -> None:
        assert isinstance(ctx, RuntimeNodeContext)
        spec = self.spec
        assert isinstance(spec, WaitSpec)
        if ctx.instance.simulated:  # v2.3 A10：模拟冻结（不订阅不建 Job）——卡点即报告发现
            return
        if spec.message is not None:
            assert ctx.token is not None
            from flowork.engine.message_flow import subscribe_open

            command_ctx = ctx.kernel.command_ctx
            assert command_ctx is not None, "等待节点订阅须在命令事务内登记"
            subscribe_open(
                command_ctx,
                ctx.kernel.engine,
                instance_id=ctx.instance.id,
                token_id=ctx.token.id,
                node_id=spec.code,
                name=spec.message.name.strip(),
                correlation_key=self._correlation_key(ctx, spec),
            )
        if spec.timeout is not None:
            schedule_wait_job(ctx, spec)

    def try_complete(self, ctx: NodeContext) -> bool:
        return False  # 等待节点永不自完成（外部触发经 finish_wait 推进）

    def on_exit(self, ctx: NodeContext) -> None:
        self._cleanup(ctx)

    def on_cancel(self, ctx: NodeContext) -> None:
        self._cleanup(ctx)

    def get_outgoing(self, ctx: NodeContext) -> list[SequenceFlow]:
        """默认路由 + wait 快照注入（求值副本，不落实例变量——spec §7.2）。"""
        from flowork.engine.route_audit import RoutingAudit, persist_route_decision
        from flowork.engine.routing import evaluation_variables, select_outgoing
        from flowork.expr.context import expression_functions_of
        from flowork.tasks.facts import attach_node

        assert isinstance(ctx, RuntimeNodeContext)
        definition = ctx.kernel.definition_of(ctx.session, ctx.instance)
        outgoing = definition.outgoing(ctx.node.code)
        engine = getattr(ctx.kernel, "engine", None)
        audit = RoutingAudit()
        variables = evaluation_variables(
            ctx.instance, ctx.variables, session=ctx.session, now=ctx.kernel.config.now()
        )
        if self.resolution is not None:
            variables = {**variables, "wait": dict(self.resolution)}
        selected = select_outgoing(
            outgoing,
            variables,
            mode="all_match",
            functions=attach_node(
                expression_functions_of(getattr(engine, "config", None)),
                ctx.session,
                ctx.instance.id,
                node_calls=audit.node_calls,
            ),
            audit=audit,
        )
        persist_route_decision(
            ctx.session,
            ctx.instance.id,
            ctx.token.id if ctx.token else None,
            audit,
        )
        return selected

    # —— 内部 ——

    def _correlation_key(self, ctx: RuntimeNodeContext, spec: WaitSpec) -> str:
        """实例上下文求值一次并快照（Zeebe 同语义）；非 string=技术故障。"""
        from flowork.engine.routing import evaluation_variables

        expression = spec.message.correlation_key if spec.message else None
        if not expression:
            return ""
        context = evaluation_variables(
            ctx.instance, ctx.variables, session=ctx.session, now=ctx.kernel.config.now()
        )
        try:
            value = evaluate_formula_value(expression, context, functions=_functions_of(ctx))
        except FormulaError as exc:
            raise EngineExecutionError(
                f"wait {spec.code} correlation_key 求值失败（{expression!r}）：{exc}"
            ) from exc
        if not isinstance(value, str):
            raise EngineExecutionError(
                f"wait {spec.code} correlation_key 须求值为 string"
                f"（{expression!r} → {type(value).__name__}）"
            )
        return value

    def _cleanup(self, ctx: NodeContext) -> None:
        """关本节点 open 订阅 + 删未触发的到时 Job（正常收尾/竞速败者共用）。"""
        assert isinstance(ctx, RuntimeNodeContext)
        assert ctx.token is not None
        from sqlalchemy import delete

        from flowork.persistence.orm import MessageSubscriptionRow

        ctx.session.execute(
            delete(MessageSubscriptionRow).where(
                MessageSubscriptionRow.instance_id == ctx.instance.id,
                MessageSubscriptionRow.node_id == ctx.node.code,
                MessageSubscriptionRow.state == "open",
            )
        )
        for job in ctx.session.scalars(select_jobs_of(ctx.instance.id)).all():
            payload = dict(job.payload or {})
            if payload.get("kind") == "wait_timeout" and payload.get("token_id") == ctx.token.id:
                ctx.session.delete(job)


def select_jobs_of(instance_id: str) -> Any:
    """实例的 scheduled timer Job 查询（清理用，避免行为层拼查询重复）。"""
    from sqlalchemy import select

    from flowork.persistence.orm import JobRow

    return select(JobRow).where(
        JobRow.instance_id == instance_id,
        JobRow.job_type == "timer",
        JobRow.state == "scheduled",
    )


def schedule_wait_job(ctx: RuntimeNodeContext, spec: WaitSpec) -> None:
    """登记到时 Job（与任务超时同款形态；暂停冻结/恢复顺延走既有 tick 规则）。"""
    import uuid

    from flowork.persistence.orm import JobRow

    assert ctx.token is not None
    assert spec.timeout is not None
    ctx.session.add(
        JobRow(
            id=uuid.uuid4().hex,
            job_type="timer",
            state="scheduled",
            created_at=ctx.kernel.config.now(),  # P1-11：与 run_at 同钟
            run_at=ctx.kernel.config.now() + timedelta(minutes=spec.timeout.after_minutes),
            instance_id=ctx.instance.id,
            payload={
                "kind": "wait_timeout",
                "activity_code": spec.code,
                "token_id": ctx.token.id,
            },
        )
    )


def finish_wait(
    kernel: Kernel,
    session: Session,
    instance: ProcessInstanceRow,
    token: TokenRow,
    *,
    result: str,
    message_name: str | None = None,
    message_id: str | None = None,
) -> None:
    """等待解决收尾（消息命中/到时/广播统一入口，同事务）。

    镜像 Kernel._finish_and_route 的手工先例（UserTaskBehavior._finish_now）：
    on_exit 清理 → 完成令牌 → node_exited → 路由（快照经 behavior_override 携带）。
    """
    from flowork.events.domain import DomainEvent

    definition = kernel.definition_of(session, instance)
    node = definition.node(token.activity_code)
    if node is None:
        raise EngineExecutionError(f"令牌节点不存在：{token.activity_code}")
    behavior = kernel.behavior_of(node)
    assert isinstance(behavior, WaitBehavior), "finish_wait 仅服务等待节点"
    behavior.resolution = {
        "result": result,
        "message_name": message_name,
        "message_id": message_id,
    }
    ctx = RuntimeNodeContext(kernel, session, instance, node, token)
    behavior.on_exit(ctx)
    kernel.finish_token(session, token, approval_exit=token.approval_exit)
    kernel.record_event(
        DomainEvent.NODE_EXITED,
        {"activity_code": node.code, "token_id": token.id},  # B4：并行分支可区分
        instance.id,
    )
    kernel.route(session, instance, node, token, behavior_override=behavior)


def _functions_of(ctx: RuntimeNodeContext) -> Any:
    from flowork.expr.context import expression_functions_of
    from flowork.tasks.facts import attach_node

    engine = getattr(ctx.kernel, "engine", None)
    functions = expression_functions_of(engine.config if engine else None)
    # node() 判断面在 correlation 位可用（catalog 位点承诺）——装配对齐
    # （code-review 2026-10 P1-8：目录放行、求值面未装配，fire 时必挂起）
    return attach_node(functions, ctx.session, ctx.instance.id)
