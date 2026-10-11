"""模拟试跑（v2.3 第三批 A10，simulation-dryrun-design.md 第二层：simulated 实例）。

- 条件边按初始变量**真求值**（与令牌模拟层相反）；
- user_task 进入即完成（approval_exit=simulated，不建任务、不走 auto_pass 票数）；
- service_task 恒 stub（**绝不执行 handler**——行业共识：模拟不得触发真实副作用）；
- cc_task 不建传阅件；call_activity 递归模拟（子实例同标记）；wait 冻结为卡点；
- 事件不投 outbox（订阅方不得对试跑做出真实反应）；不建任何 Job；
- 报告＝终态+令牌时间线+stub 清单+卡点+路由轨迹（route_audit 天然数据源）；
- 查询隔离：instances() 默认排除模拟实例。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.engine.engine import ProcessEngine
from flowork.persistence.orm import CcItemRow, JobRow, TaskRow


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _mixed_flow(engine: ProcessEngine) -> str:
    """start → a(user) → [big? svc → b] : cc → end（svc 的 handler 未注册——模拟必须 stub）。"""
    flow = (
        FlowBuilder("sim1")
        .start()
        .user_task("a", assignee="'u_a'")
        .branch("route", route_mode="first_match")
        .service_task("svc", action="NEVER_REGISTERED")
        .user_task("b", assignee="'u_b'")
        .cc_task("notify", recipients=["'u_a'"])
        .end()
        .connect("start", "a")
        .connect("a", "route")
        .flow("route", "svc", condition="amount > 100")
        .flow("route", "notify", default=True)
        .connect("svc", "b")
        .connect("b", "notify")
        .connect("notify", "end")
    )
    engine.repository.deploy(
        flow.build().model_copy(
            update={"variables": [ProcessVariable(name="amount", type="number")]}
        )
    )
    return "sim1"


def test_simulation_completes_with_stub_and_no_side_effects(engine: ProcessEngine):
    report = engine.runtime.simulate(
        "sim1" if False else _mixed_flow(engine), variables={"amount": 500}
    )

    assert report.final_state == "completed"
    assert report.visited == ["start", "a", "route", "svc", "b", "notify", "end"]
    assert report.stubbed == ["svc"]  # handler 未注册也未执行（执行即 KeyError→故障）
    assert report.stalled_at is None
    assert len(report.route_decisions) >= 2  # 路由轨迹在（route_audit 数据源）
    # 零任务/零传阅/零 Job（模拟不产生任何等待物）
    with engine.session_factory() as session:
        assert (
            session.scalars(select(TaskRow).where(TaskRow.instance_id == report.instance_id)).all()
            == []
        )
        assert (
            session.scalars(
                select(CcItemRow).where(CcItemRow.instance_id == report.instance_id)
            ).all()
            == []
        )
        assert (
            session.scalars(select(JobRow).where(JobRow.instance_id == report.instance_id)).all()
            == []
        )
    # 事件不投 outbox：订阅方不得对试跑做出真实反应
    assert engine.history.events(report.instance_id) == []


def test_simulation_condition_evaluated_with_variables(engine: ProcessEngine):
    code = _mixed_flow(engine)
    report = engine.runtime.simulate(code, variables={"amount": 1})  # 走默认边
    assert report.visited == ["start", "a", "route", "notify", "end"]
    assert report.stubbed == []
    # 真实分支（条件真求值——与令牌模拟层的显式选择相反）
    report2 = engine.runtime.simulate(code, variables={"amount": 999})
    assert "svc" in report2.visited


def test_simulation_stalls_at_wait_node(engine: ProcessEngine):
    from flowork.definitions.wait_node import WaitMessage

    flow = (
        FlowBuilder("simwait")
        .start()
        .user_task("a", assignee="'u_a'")
        .wait("w", message=WaitMessage(name="order_paid", correlation_key="'ORD-1'"))
        .user_task("after", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "w")
        .connect("w", "after")
        .connect("after", "end")
    )
    engine.repository.deploy(flow.build())
    report = engine.runtime.simulate("simwait")
    # wait 冻结为卡点：不订阅、不建到时 Job——卡点即报告发现
    assert report.final_state == "running"
    assert report.stalled_at == "w"
    with engine.session_factory() as session:
        assert (
            session.scalars(select(JobRow).where(JobRow.instance_id == report.instance_id)).all()
            == []
        )


def test_simulation_recurses_into_call_activity(engine: ProcessEngine):
    engine.repository.deploy(
        FlowBuilder("simchild")
        .start()
        .user_task("c1", assignee="'u_c'")
        .end()
        .connect("start", "c1")
        .connect("c1", "end")
        .build()
    )
    flow = (
        FlowBuilder("simparent")
        .start()
        .user_task("p1", assignee="'u_p'")
        .call_activity("sub", process_code="simchild")
        .end()
        .connect("start", "p1")
        .connect("p1", "sub")
        .connect("sub", "end")
    )
    engine.repository.deploy(flow.build())
    report = engine.runtime.simulate("simparent")
    assert report.final_state == "completed"
    # 子实例同 simulated 标记且无事件（递归模拟）
    child = next(
        i
        for i in engine.history.instances(include_simulated=True)
        if i.parent_id == report.instance_id
    )
    assert child.simulated is True
    assert engine.history.events(child.id) == []


def test_instances_query_isolates_simulated_by_default(engine: ProcessEngine):
    code = _mixed_flow(engine)
    report = engine.runtime.simulate(code, variables={"amount": 1})
    visible = engine.history.instances()
    assert all(i.id != report.instance_id for i in visible)
    included = engine.history.instances(include_simulated=True)
    assert any(i.id == report.instance_id for i in included)


def test_real_start_unaffected_by_simulation_paths(engine: ProcessEngine):
    code = _mixed_flow(engine)
    instance_id = engine.runtime.start_process(code, start_user="u_real", variables={"amount": 1})
    # 真实启动：等待在 a（任务在、事件在）
    tasks = engine.task.list_tasks(instance_id=instance_id)
    assert [t.activity_code for t in tasks] == ["a"]
    assert engine.history.events(instance_id) != []


# —— 草稿直通（设计器插件计划 A1）：证据先于发布 ——


def _draft_spec_dict(engine: ProcessEngine, code: str) -> dict[str, Any]:
    """构造未部署的草稿 spec dict（模拟的直通入参形态与设计器一致）。"""
    from flowork.definitions.definition import dump_definition

    flow = (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_a'")
        .branch("route", route_mode="first_match")
        .service_task("svc", action="NEVER_REGISTERED")
        .user_task("b", assignee="'u_b'")
        .cc_task("notify", recipients=["'u_a'"])
        .end()
        .connect("start", "a")
        .connect("a", "route")
        .flow("route", "svc", condition="amount > 100")
        .flow("route", "notify", default=True)
        .connect("svc", "b")
        .connect("b", "notify")
        .connect("notify", "end")
    )
    built = flow.build().model_copy(
        update={"variables": [ProcessVariable(name="amount", type="number")]}
    )
    return dump_definition(built)


def test_simulation_draft_spec_never_deploys(engine: ProcessEngine):
    """草稿模拟不落定义表：报告产出但 code 仍查无已发布定义。"""
    spec = _draft_spec_dict(engine, "simdraft")
    report = engine.runtime.simulate("simdraft", spec=spec, variables={"amount": 500})

    assert report.final_state == "completed"
    assert report.definition_code == "simdraft"
    assert report.visited == ["start", "a", "route", "svc", "b", "notify", "end"]
    assert report.stubbed == ["svc"]
    from flowork.errors import DefinitionNotPublishedError

    with pytest.raises(DefinitionNotPublishedError):
        engine.repository.get_definition("simdraft")


def test_simulation_draft_spec_isolation_semantics(engine: ProcessEngine):
    """草稿模拟沿用全部隔离语义：实例列表默认不可见、版本列草稿占位、零事件。"""
    spec = _draft_spec_dict(engine, "simdraft")
    report = engine.runtime.simulate("simdraft", spec=spec, variables={"amount": 1})

    assert all(i.id != report.instance_id for i in engine.history.instances())
    visible = engine.history.instances(include_simulated=True)
    row = next(i for i in visible if i.id == report.instance_id)
    assert (row.definition_major, row.definition_revision) == (0, 0)  # 草稿占位
    assert row.simulated is True
    assert engine.history.events(report.instance_id) == []


def test_simulation_draft_spec_wins_over_published(engine: ProcessEngine):
    """spec 与已发布版本并存时按 spec 内容走：改条件阈值即改路由，不碰发布版。"""
    engine.repository.deploy(
        FlowBuilder("simboth")
        .start()
        .user_task("a", assignee="'u_a'")
        .branch("route", route_mode="first_match")
        .user_task("high", assignee="'u_h'")
        .user_task("low", assignee="'u_l'")
        .end()
        .connect("start", "a")
        .connect("a", "route")
        .flow("route", "high", condition="amount > 100")
        .flow("route", "low", default=True)
        .connect("high", "end")
        .connect("low", "end")
        .build()
        .model_copy(update={"variables": [ProcessVariable(name="amount", type="number")]})
    )
    # 草稿把阈值改到 10：amount=50 发布版走 low、草稿走 high
    from flowork.definitions.definition import dump_definition

    draft = dump_definition(
        FlowBuilder("simboth")
        .start()
        .user_task("a", assignee="'u_a'")
        .branch("route", route_mode="first_match")
        .user_task("high", assignee="'u_h'")
        .user_task("low", assignee="'u_l'")
        .end()
        .connect("start", "a")
        .connect("a", "route")
        .flow("route", "high", condition="amount > 10")
        .flow("route", "low", default=True)
        .connect("high", "end")
        .connect("low", "end")
        .build()
        .model_copy(update={"variables": [ProcessVariable(name="amount", type="number")]})
    )
    report = engine.runtime.simulate("simboth", spec=draft, variables={"amount": 50})
    assert "high" in report.visited
    # 发布版未受影响：按已发布定义模拟仍走 low
    published_report = engine.runtime.simulate("simboth", variables={"amount": 50})
    assert "low" in published_report.visited


def test_simulation_draft_spec_enforces_input_contract(engine: ProcessEngine):
    """草稿模拟同受 input 契约把关（start 边界）：缺必填键报错不入库。"""
    from flowork.definitions.contract import InputContractError

    spec = _draft_spec_dict(engine, "simcontract")
    spec["input"] = {
        "type": "object",
        "properties": {"amount": {"type": "number"}},
        "required": ["amount"],
    }
    with pytest.raises(InputContractError):
        engine.runtime.simulate("simcontract", spec=spec, variables={})
    # 报错整笔回滚：不留半截实例
    assert all(
        i.definition_code != "simcontract" for i in engine.history.instances(include_simulated=True)
    )


def test_simulation_draft_spec_rejects_junk(engine: ProcessEngine):
    from flowork.definitions.definition import InvalidDefinitionData

    with pytest.raises(InvalidDefinitionData):
        engine.runtime.simulate("simjunk", spec={"nodes": "not-a-list"})
