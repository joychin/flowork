"""能力域·变量读写（变量 I/O 批）：集成方视角黑盒——改值改道、终态拒、血缘可查。

场景流（测试内经公共 API 部署，不入 world_catalog）：提交 → 金额分流 →
高额道（≥1万，u_chief）/ 低额道直达结束。分支在用户任务之后——外部改值
必须影响「后续」求值位点。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.engine.engine import ProcessEngine
from flowork.errors import InstanceNotFoundError
from tests.suite.features import covers
from tests.suite.walkthrough import complete_of


def deploy_reroute_flow(engine: ProcessEngine) -> None:
    definition = (
        FlowBuilder("suite_variable_reroute", "套件·外部改值改道")
        .start()
        .user_task("submit", "提交", assignee="'u_sub'")
        .branch("gate", "金额分流")
        .user_task("chief", "高额审批", assignee="'u_chief'")
        .end()
        .flow("start", "submit")
        .flow("submit", "gate")
        .flow("gate", "chief", condition="amount >= 10000")
        .flow("gate", "end", default=True)
        .flow("chief", "end")
        .build()
    )
    definition.variables = [
        ProcessVariable(name="amount", type="number", label="金额", sample=5000)
    ]
    engine.repository.deploy(definition)


def start_low(engine: ProcessEngine) -> str:
    return engine.runtime.start_process(
        "suite_variable_reroute", start_user="u_sub", variables={"amount": 5000}
    )


@covers("variables.external_update", "variables.reroute_pending_branch")
def test_external_update_reroutes_pending_branch(suite_engine: ProcessEngine) -> None:
    """钱场景：等待中的提交任务被外部改大金额 → 提交后按新值走高额道。"""
    engine = suite_engine
    deploy_reroute_flow(engine)
    iid = start_low(engine)

    engine.runtime.update_variables(iid, {"amount": 50000}, actor="u_risk", reason="风控刷新信用分")
    assert engine.runtime.variables_of(iid)["amount"] == 50000
    # variables_updated 事件入 outbox（actor 与 reason 归因）
    names = [e.event_name for e in engine.history.events(instance_id=iid, limit=50)]
    assert "variables_updated" in names

    complete_of(engine, iid, "u_sub")  # 提交后按新值分流
    ready = {t.assignee for t in engine.task.list_tasks(instance_id=iid)}
    assert ready == {"u_chief"}  # 50000 ≥ 1万 → 高额道（原值 5000 会直达结束）


@covers("variables.terminal_guard", "variables.history")
def test_terminal_rejected_and_lineage_visible(suite_engine: ProcessEngine) -> None:
    """终态实例拒改；血缘表给出 birth→外部改值全链路与归因。"""
    engine = suite_engine
    deploy_reroute_flow(engine)
    iid = start_low(engine)
    engine.runtime.update_variables(iid, {"amount": 20000}, actor="u_risk")
    complete_of(engine, iid, "u_sub")
    complete_of(engine, iid, "u_chief")  # 走完高额道 → 终态
    assert engine.runtime.get_instance(iid).state == "completed"

    with pytest.raises(InstanceNotFoundError, match="终态"):
        engine.runtime.update_variables(iid, {"amount": 1}, actor="u_risk")

    lineage = [
        (r.name, r.old_value, r.new_value, r.source, r.acted_by)
        for r in engine.runtime.variable_history(iid, name="amount")
    ]
    assert lineage == [
        ("amount", 5000, 20000, "external_update", "u_risk"),
        ("amount", None, 5000, "engine", None),
    ]
