"""RuntimeService：流程实例生命周期（发起 / 取消 / 查询）。"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from flowork.engine.incident_service import IncidentMixin
from flowork.engine.instance_lifecycle import InstanceLifecycleMixin, _load, _to_info
from flowork.engine.ops import CommandContext
from flowork.engine.variable_actions import VariableActionsMixin
from flowork.errors import DuplicateStartError, InstanceNotFoundError
from flowork.events.domain import DomainEvent
from flowork.tasks.models import InstanceInfo

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["RuntimeService"]


class RuntimeService(InstanceLifecycleMixin, IncidentMixin, VariableActionsMixin):
    """实例运行时服务。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine

    def start_process(
        self,
        code: str,
        *,
        start_user: str,
        variables: dict[str, Any] | None = None,
        business_key: str | None = None,
        major: int | None = None,
        revision: int | None = None,
        idempotency_key: str | None = None,
        reuse: Literal["reject", "return_existing", "allow_duplicate"] = "reject",
        simulated: bool = False,
    ) -> str:
        """发起流程（使用已发布定义的快照）。返回实例 ID。

        Args:
            code: 已发布流程定义编码。
            start_user: 发起人（公式保留标识符 ``initiator`` 解析为此值）。
            variables: 流程变量（表单数据/路由条件依据）。
            business_key: 业务关联键（如订单号），供外部对账。
            major/revision: 显式钉版本启动；缺省＝启动事务内
                SELECT 最新 published（deploy 前旧版、之后新版，变更边界＝deploy 时刻）。
            idempotency_key/reuse: 启动幂等三策略——reject（撞 key 报
                DuplicateStartError，默认）/ return_existing（返回已有实例）/
                allow_duplicate（明示放弃幂等）。key 与 definition_code 联合唯一，
                并发撞车由唯一索引兜底后按策略收口。
        """

        def _command(ctx: CommandContext) -> str:
            return self._start(
                ctx,
                code,
                start_user,
                variables,
                business_key,
                None,
                None,
                major,
                revision,
                idempotency_key,
                reuse,
                simulated=simulated,
            )

        try:
            return self._engine.execute(_command)
        except IntegrityError as exc:
            # 并发同 key 撞联合唯一索引：败者事务已回滚，新事务重查后按策略收口
            if idempotency_key is None or reuse == "allow_duplicate":
                raise
            existing = self._engine.execute(
                lambda ctx: self._existing_instance(ctx, code, idempotency_key)
            )
            if existing is None:
                raise
            if reuse == "reject":
                raise DuplicateStartError(
                    f"启动幂等键冲突（并发）：{code}#{idempotency_key} 已有实例 {existing}"
                ) from exc
            return existing

    def _existing_instance(self, ctx: CommandContext, code: str, key: str) -> str | None:
        """按 (definition_code, idempotency_key) 查已有实例。"""

        from flowork.persistence.orm import ProcessInstanceRow as _Row

        found = ctx.session.scalars(
            select(_Row.id).where(_Row.definition_code == code, _Row.idempotency_key == key)
        ).first()
        return found

    def _start(
        self,
        ctx: CommandContext,
        code: str,
        start_user: str,
        variables: dict[str, Any] | None,
        business_key: str | None,
        parent_instance_id: str | None,
        parent_token_id: str | None,
        major: int | None,
        revision: int | None,
        idempotency_key: str | None,
        reuse: Literal["reject", "return_existing", "allow_duplicate"],
        start_message: dict[str, str] | None = None,
        simulated: bool = False,
        spec: dict[str, Any] | None = None,
    ) -> str:
        import json

        from flowork.persistence.orm import ProcessInstanceRow as _Row

        if idempotency_key is not None and reuse != "allow_duplicate":
            existing = self._existing_instance(ctx, code, idempotency_key)
            if existing is not None:
                if reuse == "reject":
                    raise DuplicateStartError(
                        f"启动幂等键冲突：{code}#{idempotency_key} 已有实例 {existing}"
                    )
                return existing

        if spec is not None:
            # 草稿直通：不落定义表，版本列记草稿占位 (0, 0)——
            # 仅供 simulate 消费（真实实例仍只能来自已发布定义，start_process 不开放 spec）
            from flowork.definitions.definition import load_definition

            snapshot = {**spec, "code": code, "major": 0, "revision": 0}
            definition = load_definition(snapshot, self._engine.config.node_registry)
        else:
            snapshot = self._engine.repository.get_definition_json(code, major, revision)
            definition = self._engine.repository.get_definition(code, major, revision)
        # input 契约边界①：发起时按 schema 拒收（报错不入库）
        from flowork.definitions.contract import validate_input_contract

        validate_input_contract(definition.input, dict(variables or {}), boundary="start")
        instance = _Row(
            id=uuid.uuid4().hex,
            definition_code=code,
            definition_major=snapshot["major"],
            definition_revision=snapshot["revision"],
            definition_snapshot=json.dumps(snapshot, ensure_ascii=False),
            business_key=business_key,
            # allow_duplicate＝明示放弃幂等：不落 key（联合唯一索引下同 key 只能一行）
            idempotency_key=idempotency_key if reuse != "allow_duplicate" else None,
            state="running",
            # 业务时刻单源 config.now（与 updated_at/finished_at/deadline_at 同钟）
            started_at=self._engine.config.now(),
            start_user=start_user,
            variables=dict(variables or {}),
            parent_id=parent_instance_id,
            parent_token_id=parent_token_id,
            simulated=simulated,
        )
        ctx.session.add(instance)
        if simulated:  # 模拟静默（事件不投 outbox；不建实例时限 Job）
            ctx.suppress_events = True
        # 变量留痕：出生 before 显式播种为空——发起变量记 birth 行（old=None）
        ctx.variable_before[instance.id] = {}
        # 实例时限物化——按 after_minutes 折算 deadline_at（自然时间）
        if definition.deadline is not None:
            from datetime import timedelta

            from flowork.persistence.orm import JobRow

            instance.deadline_at = self._engine.config.now() + timedelta(
                minutes=definition.deadline.after_minutes
            )
        ctx.session.flush()
        if definition.deadline is not None and not simulated:
            import uuid as _uuid

            from flowork.persistence.orm import JobRow

            ctx.session.add(
                JobRow(
                    id=_uuid.uuid4().hex,
                    job_type="timer",
                    state="scheduled",
                    created_at=self._engine.config.now(),  # 与 run_at 同钟
                    run_at=instance.deadline_at,
                    instance_id=instance.id,
                    payload={
                        "kind": "instance_deadline",
                        "strategy": definition.deadline.strategy.value,
                    },
                )
            )
        kernel = self._engine.kernel_for(ctx)
        started_payload: dict[str, Any] = {
            "definition_code": code,
            "major": instance.definition_major,  # 实例钉的版本事件面可见
            "revision": instance.definition_revision,
            "start_user": start_user,
            "business_key": business_key,
        }
        if start_message is not None:  # 消息启动：归因可追溯
            started_payload["start_message"] = start_message
        kernel.record_event(DomainEvent.PROCESS_STARTED, started_payload, instance.id)
        start_node = definition.start_node()
        if start_node is None:
            raise InstanceNotFoundError(f"定义 {code} 缺少 start 节点")
        kernel.activate(ctx.session, instance, start_node)
        return instance.id

    def simulate(
        self,
        code: str,
        *,
        variables: dict[str, Any] | None = None,
        start_user: str = "simulator",
        major: int | None = None,
        revision: int | None = None,
        spec: dict[str, Any] | None = None,
    ) -> Any:
        """整图试跑：模拟启动并由机器人驱动到卡点/终态，返回 SimulationReport。

        条件边按初始变量**真求值**；user_task 建真实任务行、机器人缺省全通过
        （办理走真实任务动作面——会签/协办语义与真实运行同源）；service_task 恒
        stub、wait 冻结为卡点、事件不投 outbox——报告复用路由审计
        （wf_route_decision）+ 令牌时间线 + 已办行。

        ``spec``（草稿直通）：传入定义 JSON 则**不查库**，按
        草稿内容模拟——证据先于发布（AI 改完草稿 → 人模拟 → 拍板发布）。
        实例行版本列记草稿占位 (0, 0)，隔离语义与发布版模拟完全一致；
        major/revision 与 spec 互斥（spec 优先）。
        """
        from flowork.engine.simulation import build_report
        from flowork.engine.simulation_runner import drive_simulation

        if spec is not None:
            spec_user = start_user
            spec_vars = variables

            def _command(ctx: CommandContext) -> str:
                return self._start(
                    ctx,
                    code,
                    spec_user,
                    spec_vars,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "reject",
                    simulated=True,
                    spec=spec,
                )

            instance_id = self._engine.execute(_command)
        else:
            instance_id = self.start_process(
                code,
                start_user=start_user,
                variables=variables,
                major=major,
                revision=revision,
                simulated=True,
            )
        # 先驱动到停点再出报告（终态/令牌时间线要反映驱动后的实况）
        notes = drive_simulation(self._engine, instance_id)
        report = build_report(self._engine, instance_id)
        report.notes = notes
        return report

    def get_instance(self, instance_id: str) -> InstanceInfo:
        def _command(ctx: CommandContext) -> Any:
            return _to_info(_load(ctx, instance_id))

        result: InstanceInfo = self._engine.execute(_command)
        return result
