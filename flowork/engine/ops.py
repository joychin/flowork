"""Command 管线：所有引擎操作经拦截器链执行（借鉴 Activiti/Camunda）。

拦截器顺序（外→内）：Metrics（计时）→ Transaction（提交/回滚）→
LockRetry（乐观锁冲突重试）→ HistoryFlush（事件缓冲落 outbox）→ 业务命令。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy.orm.exc import ObjectDeletedError

from flowork.engine.kernel import EngineExecutionError
from flowork.errors import FloworkError, TaskNotAvailableError
from flowork.persistence.orm import EventOutboxRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessInstanceRow

__all__ = ["CommandContext", "LockConflictError", "execute_command"]

logger = logging.getLogger("flowork")

T = TypeVar("T")


class LockConflictError(FloworkError):
    """乐观锁冲突且重试耗尽。"""


@dataclass
class CommandContext:
    """一次命令的执行环境：会话 + 事件缓冲 + 触及的实例（Incident 归因）。"""

    engine: ProcessEngine
    session: Session
    events: list[tuple[str, dict[str, Any], str | None]] = field(default_factory=list)
    instances_touched: set[str] = field(default_factory=set)
    # —— 变量留痕（变量 I/O 批）：before 快照 + 命令级写方归因 ——
    variable_before: dict[str, dict[str, Any]] = field(default_factory=dict)
    write_source: str = "engine"
    write_actor: str | None = None
    write_reason: str | None = None
    # 模拟实例的事件静默（record_event 直通丢弃——不投 outbox，
    # 杜绝订阅方对试跑做出真实反应；route_decision 行不受影响=报告数据源）
    suppress_events: bool = False

    def touch(self, instance: ProcessInstanceRow) -> None:
        """写命令统一守卫：触碰实例行（updated_at），提交时触发 version 乐观锁。

        所有修改实例域内数据（任务/令牌/传阅/Job 副作用）的写命令都必须调用，
        使并发写在同一实例行上串行化；同时纳入 Incident 归因集合与变量留痕快照。
        """
        instance.updated_at = self.engine.config.now()
        self.instances_touched.add(instance.id)
        self.snapshot_variables(instance.id, row=instance)

    def snapshot_variables(
        self, instance_id: str, *, row: ProcessInstanceRow | None = None
    ) -> None:
        """首次触及实例时留存变量改前快照（留痕 diff 的 before 侧，幂等）。

        咽喉点两处：``touch``（服务级写命令）与 ``Kernel.record_event``
        （钩子路径不经过 touch，双覆盖才完整）。deepcopy 防原地写污染快照，
        开销受实例变量 1MB 约束。
        """
        if instance_id in self.variable_before:
            return
        if row is None:
            from flowork.persistence.orm import ProcessInstanceRow

            row = self.session.get(ProcessInstanceRow, instance_id)
        import copy

        self.variable_before[instance_id] = copy.deepcopy(dict(row.variables)) if row else {}


def _sync_variable_index(ctx: CommandContext) -> None:
    """变量索引同步（spec §14）：触及实例的 searchable 变量同事务重写。"""
    from flowork.persistence.orm import ProcessInstanceRow
    from flowork.persistence.variable_index import sync_variable_index

    for instance_id in ctx.instances_touched:
        instance = ctx.session.get(ProcessInstanceRow, instance_id)
        if instance is not None:
            sync_variable_index(ctx.session, instance)


def _sync_variable_history(ctx: CommandContext) -> None:
    """变量留痕（变量 I/O 批，spec §14）：触及实例逐键 diff → wf_variable_history。

    全写方覆盖（发起/任务提交/钩子/消息/映射/外部命令），值相等跳过；
    仅外部更新命令（write_source=external_update）追发 variables_updated 事件
    （spec §10：引擎内部写变量不发——防刷屏，血缘走表）。
    """
    import uuid

    from flowork.events.domain import DomainEvent
    from flowork.persistence.orm import ProcessInstanceRow, VariableHistoryRow

    for instance_id in sorted(ctx.instances_touched):
        instance = ctx.session.get(ProcessInstanceRow, instance_id)
        if instance is None:
            continue
        before = ctx.variable_before.get(instance_id, {})
        after = dict(instance.variables or {})
        changes: list[dict[str, Any]] = []
        for name in sorted(set(before) | set(after)):
            old, new = before.get(name), after.get(name)
            if old == new:
                continue
            ctx.session.add(
                VariableHistoryRow(
                    id=uuid.uuid4().hex,
                    instance_id=instance_id,
                    name=name,
                    old_value=old,
                    new_value=new,
                    source=ctx.write_source,
                    acted_by=ctx.write_actor,
                )
            )
            changes.append({"name": name, "old": old, "new": new})
        if changes and ctx.write_source == "external_update":
            ctx.events.append(
                (
                    DomainEvent.VARIABLES_UPDATED,
                    {
                        "actor": ctx.write_actor,
                        "reason": ctx.write_reason,
                        "changes": changes,
                    },
                    instance_id,
                )
            )


def _flush_history(ctx: CommandContext) -> None:
    """HistoryFlush 拦截器：把命令期间缓冲的领域事件写入 outbox（同事务）。

    全序与时钟：occurred_at＝命令时刻统一取
    ``config.now()``（与任务/令牌同源，宿主假钟下不再分叉）；行 id 用引擎的
    可排序串（时间前缀+nonce+计数），消费方 ``ORDER BY (occurred_at, id)``
    即事件全序——同命令内按发射顺序，不再受列默认墙钟的微秒平局随机定序。
    """
    from flowork.engine.config import should_record_event

    stamp = ctx.engine.config.now()
    for name, payload, instance_id in ctx.events:
        if not should_record_event(ctx.engine.config, name):
            continue
        ctx.session.add(
            EventOutboxRow(
                id=ctx.engine.next_event_id(stamp),
                event_name=name,
                instance_id=instance_id,
                payload=payload,
                occurred_at=stamp,
            )
        )
    ctx.session.flush()


def _record_incidents(
    engine: ProcessEngine, ctx: CommandContext, error: EngineExecutionError
) -> None:
    """引擎执行错误 → 记录 Incident 并挂起实例（独立事务，失败不掩蔽原错误）。"""
    if not ctx.instances_touched:
        return
    try:
        engine.runtime._record_incident(list(ctx.instances_touched), error)
    except Exception:
        logger.exception("Incident 记录失败")


def _run_once(engine: ProcessEngine, command: Callable[[CommandContext], T]) -> T:
    session = engine.session_factory()
    ctx = CommandContext(engine=engine, session=session, events=[])
    try:
        # Transaction 拦截器：单命令单事务
        with session.begin():
            result = command(ctx)
            _sync_variable_index(ctx)
            _sync_variable_history(ctx)
            _flush_history(ctx)
        return result
    except EngineExecutionError as error:
        _record_incidents(engine, ctx, error)  # 回滚后以独立事务记录 Incident
        raise
    except RecursionError as error:
        # 深直通链推进递归栈溢出（实测约 7 帧/节点、直通链 200 节点崩）：归一技术故障，
        # 与其他引擎错误同管线（挂起+故障单；start 阶段整笔回滚时与「首节点失败」同语义）。
        # 兄弟 except 不互捕，需显式记录后再抛。迭代化根治挂账内核重排刀（§15）。
        converted = EngineExecutionError(
            "流程推进深度超限（深直通链递归栈溢出），建议拆分子流程——"
            "设计期 graph.flow.large 告警可提前提示"
        )
        _record_incidents(engine, ctx, converted)
        raise converted from error
    except ObjectDeletedError as error:
        # 并发删行归一（tests/edge submit vs timer 实证残留窗）：
        # 对手在本命令 claim-flush 之后提交了同一任务行的删除——claim 咽喉的
        # expire 复验看不到这个时刻。任务行被并发删除＝已被处理，归一领域错误
        # （重试只会再次失败：行已不存在）。
        raise TaskNotAvailableError(f"任务已被并发处理：{error}") from error
    finally:
        session.close()


def execute_command(engine: ProcessEngine, command: Callable[[CommandContext], T]) -> T:
    """执行一条引擎命令（Metrics → LockRetry → Transaction → HistoryFlush）。"""
    started = time.perf_counter()  # Metrics 拦截器（默认仅记录调试日志）
    attempts = max(1, engine.config.lock_retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = _run_once(engine, command)
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.debug("command ok attempt=%d elapsed_ms=%.1f", attempt, elapsed_ms)
            return result
        except Exception as exc:  # LockRetry 拦截器：仅对乐观锁冲突重试
            if not _is_lock_conflict(exc):
                raise
            last_error = exc
            logger.warning("乐观锁冲突，第 %d/%d 次重试", attempt, attempts)
    raise LockConflictError(f"乐观锁冲突重试 {attempts} 次后仍失败") from last_error


def _is_lock_conflict(exc: Exception) -> bool:
    """SQLAlchemy 乐观锁（version_id）冲突的判定。"""
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm.exc import StaleDataError

    if isinstance(exc, StaleDataError):
        return True
    if isinstance(exc, SQLAlchemyError):
        return "database is locked" in str(exc).lower()
    return False
