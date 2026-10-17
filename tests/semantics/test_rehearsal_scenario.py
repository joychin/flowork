"""排练与场景回归（批 3/批 4）：交互驱动、步单快照、脚本运行、断言、覆盖。

排练＝simulated 实例的人点驱动（真实任务行 + 真实动作面，隔离全同）；
场景＝同一基座上的线性脚本（与演练剧本同构）——一份格式三个消费者
（设计器面板 / CLI scenario run / 测试代码），Camunda「设计器用例=CI 用例」
同款范式。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.engine import rehearsal
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.engine.scenario import parse_scenario, run_scenario
from flowork.errors import FloworkError


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _approval(engine: ProcessEngine, code: str = "reh") -> None:
    flow = (
        FlowBuilder(code)
        .start()
        .user_task("submit", assignee="'u_zhang'")
        .user_task("manager", assignee="'u_manager'", reject_target="submit")
        .end()
        .connect("start", "submit")
        .connect("submit", "manager")
        .connect("manager", "end")
    )
    engine.repository.deploy(flow.build())


# —— 排练（批 3） ——


def test_rehearsal_step_and_state(engine: ProcessEngine):
    """步单快照四面板 + 人点一步：待办换了、已办记上、路由落行。"""
    _approval(engine)
    iid = rehearsal.start_rehearsal(engine, "reh", start_user="u_zhang")
    state = rehearsal.rehearsal_state(engine, iid)
    assert [t["activity_code"] for t in state["tasks"]] == ["submit"]
    assert "complete" in state["tasks"][0]["available_actions"]
    task_id = state["tasks"][0]["id"]
    rehearsal.perform_rehearsal_step(
        engine, iid, {"task_id": task_id, "action": "complete", "comment": "提交"}
    )
    state = rehearsal.rehearsal_state(engine, iid)
    assert [t["activity_code"] for t in state["tasks"]] == ["manager"]
    assert [(h["node"], h["outcome"], h["acted_by"]) for h in state["task_history"]] == [
        ("submit", "agree", "u_zhang")
    ]
    assert any(row["node"] == "submit" and row["selected"] for row in state["routes"])


def test_rehearsal_isolated_from_business_surfaces(engine: ProcessEngine):
    """排练实例的任务不进业务待办（与模拟同款隔离）。"""
    _approval(engine)
    iid = rehearsal.start_rehearsal(engine, "reh", start_user="u_zhang")
    assert engine.task.list_tasks() == []
    assert [t["activity_code"] for t in rehearsal.rehearsal_state(engine, iid)["tasks"]] == [
        "submit"
    ]
    assert engine.history.events(iid) == []


def test_rehearsal_auto_claims_candidate_pool(engine: ProcessEngine):
    """候选池任务（未认领）：排练者直接办理（认领是流程细节不是演练关卡）。"""
    engine.repository.deploy(
        FlowBuilder("rehcand")
        .start()
        .user_task("pick", candidate_users=["'u_x'"])
        .end()
        .connect("start", "pick")
        .connect("pick", "end")
        .build()
    )
    iid = rehearsal.start_rehearsal(engine, "rehcand")
    task = rehearsal.rehearsal_state(engine, iid)["tasks"][0]
    assert task["assignee"] == ""
    rehearsal.perform_rehearsal_step(
        engine, iid, {"task_id": task["id"], "action": "complete", "by": "u_x"}
    )
    assert engine.runtime.get_instance(iid).state == "completed"


def test_rehearsal_wait_step(engine: ProcessEngine):
    """等待步：按假设兑现冻结的等待节点（workbench 的等待不是死路）。"""
    engine.repository.deploy(
        FlowBuilder("rehwait")
        .start()
        .wait("w", message={"name": "paid", "correlation_key": "'K'"})
        .user_task("after", assignee="'u_a'")
        .end()
        .connect("start", "w")
        .connect("w", "after")
        .connect("after", "end")
        .build()
    )
    iid = rehearsal.start_rehearsal(engine, "rehwait")
    state = rehearsal.rehearsal_state(engine, iid)
    assert state["waiting"] == [{"node": "w", "instance_id": iid}]
    rehearsal.perform_rehearsal_step(
        engine, iid, {"kind": "wait", "node": "w", "result": "matched"}
    )
    assert [t["activity_code"] for t in rehearsal.rehearsal_state(engine, iid)["tasks"]] == [
        "after"
    ]


def test_rehearsal_rejects_foreign_task(engine: ProcessEngine):
    """跨实例任务拒绝（树校验）：排练者不能操作别的实例。"""
    _approval(engine, "reh_a")
    _approval(engine, "reh_b")
    iid_a = rehearsal.start_rehearsal(engine, "reh_a")
    iid_b = rehearsal.start_rehearsal(engine, "reh_b")
    task_b = rehearsal.rehearsal_state(engine, iid_b)["tasks"][0]
    with pytest.raises(FloworkError, match="不属于该排练实例"):
        rehearsal.perform_rehearsal_step(
            engine, iid_a, {"task_id": task_b["id"], "action": "complete"}
        )


# —— 场景回归（批 4） ——


def _scenario_engine(engine: ProcessEngine) -> None:
    flow = (
        FlowBuilder("scn")
        .start()
        .user_task("submit", assignee="'u_zhang'")
        .user_task("manager", assignee="'u_manager'", reject_target="submit")
        .branch("route")
        .user_task("big", assignee="'u_big'")
        .user_task("small", assignee="'u_small'")
        .end()
        .connect("start", "submit")
        .connect("submit", "manager")
        .connect("manager", "route")
        .flow("route", "big", condition="amount > 1000")
        .flow("route", "small", default=True)
        .connect("big", "end")
        .connect("small", "end")
    )
    built = flow.build().model_copy(
        update={"variables": [ProcessVariable(name="amount", type="number")]}
    )
    engine.repository.deploy(built)


def test_scenario_runs_green_with_assertions(engine: ProcessEngine):
    """场景全绿：驳回回退 + 改额 + 条件真求值 + 断言全过 + 覆盖照明。"""
    _scenario_engine(engine)
    report = run_scenario(
        engine,
        "scn",
        {
            "name": "改额后走大额分支",
            "start_user": "u_zhang",
            "variables": {"amount": 500},
            "steps": [
                {"node": "submit", "by": "u_zhang", "action": "complete"},
                {"node": "manager", "by": "u_manager", "action": "reject", "comment": "补料"},
                {
                    "node": "submit",
                    "by": "u_zhang",
                    "action": "complete",
                    "variables": {"amount": 5000},
                },
                {"node": "manager", "by": "u_manager", "action": "complete"},
                {"node": "big", "by": "u_big", "action": "complete"},
            ],
            "assertions": [
                {"type": "final_state", "equals": "completed"},
                {"type": "task", "node": "manager", "outcome": "reject", "count": 1},
                {"type": "variable", "name": "amount", "op": ">", "value": 1000},
                {"type": "route", "node": "route", "selected": "route->big"},
                {"type": "visited", "not_contains": ["small"]},
                {"type": "pending", "empty": True},
            ],
        },
    )
    assert report.ok, report.assertions
    assert len(report.assertions) == 6
    assert all(item["ok"] for item in report.assertions)
    assert report.coverage["nodes"]["total"] == 7  # start/submit/manager/route/big/small/end
    assert "small" in report.coverage["nodes"]["missing"]  # 未走的一支=照明发现


def test_scenario_red_on_failed_assertion(engine: ProcessEngine):
    """断言失败=红（ok False + failed 明细）——CI 退出码的依据。"""
    _scenario_engine(engine)
    report = run_scenario(
        engine,
        "scn",
        {
            "start_user": "u_zhang",
            "variables": {"amount": 5000},
            "steps": [],
            "assertions": [{"type": "final_state", "equals": "completed"}],
        },
    )
    assert not report.ok
    assert [item["type"] for item in report.failed] == ["final_state"]
    assert report.failed[0]["actual"] == "running"  # 未推进：停在该用户的待办


def test_scenario_wait_and_clock_steps(engine: ProcessEngine):
    """脚本混排三类步：等待兑现 + 推钟（真实策略面兑现）。"""
    engine.repository.deploy(
        FlowBuilder("scnmix")
        .start()
        .wait(
            "w",
            message={"name": "paid", "correlation_key": "'K'"},
            timeout={"after_minutes": 60},
        )
        .user_task("late", assignee="'u_late'")
        .end()
        .connect("start", "w")
        .flow("w", "late", condition='wait.result == "elapsed"')
        .flow("w", "end", default=True)
        .connect("late", "end")
        .build()
    )
    report = run_scenario(
        engine,
        "scnmix",
        {
            "steps": [
                {"clock": {"advance_minutes": 120}},
                {"node": "late", "by": "u_late", "action": "complete"},
            ],
            "assertions": [
                {"type": "final_state", "equals": "completed"},
                {"type": "visited", "contains": ["late"]},
            ],
        },
    )
    assert report.ok, (report.assertions, report.notes)
    assert report.advanced_minutes == 120


def test_scenario_parse_rejects_junk():
    """脚本形状校验：缺 node/wait/clock 的步、缺 type 的断言。"""
    with pytest.raises(FloworkError, match="node/wait/clock"):
        parse_scenario({"steps": [{"action": "complete"}]})
    with pytest.raises(FloworkError, match="缺少 type"):
        parse_scenario({"assertions": [{"equals": 1}]})
    with pytest.raises(FloworkError, match="必须"):
        parse_scenario("not-a-dict")


def test_scenario_stops_on_bad_step_and_reports(engine: ProcessEngine):
    """坏步=诚实停点：notes 带原因，断言按实际状态判（不抛）。"""
    _scenario_engine(engine)
    report = run_scenario(
        engine,
        "scn",
        {
            "start_user": "u_zhang",
            "variables": {"amount": 5000},
            "steps": [{"node": "nonexistent", "action": "complete"}],
            "assertions": [{"type": "final_state", "equals": "completed"}],
        },
    )
    assert not report.ok
    assert any("脚本停在失败步" in note for note in report.notes)


def test_scenario_timeout_reassign_end_to_end(tmp_path):
    """DoD：超时改派全链路（示例定义+示例场景，推钟兑现真实策略）全绿。"""
    import json
    from pathlib import Path

    from flowork.api import create_registry, load_definition_file

    examples = Path(__file__).parent.parent.parent / "examples"
    engine = ProcessEngine(EngineConfig(db_file=str(tmp_path / "dod.db")))
    try:
        engine.repository.deploy(
            load_definition_file(examples / "05_timeout_reassign.json", create_registry())
        )
        script = json.loads((examples / "05_timeout_scenario.json").read_text(encoding="utf-8"))
        report = run_scenario(engine, "approval_timeout", script)
        assert report.ok, (report.assertions, report.notes)
        assert report.advanced_minutes == 2880
        assert report.coverage["nodes"]["total"] == 6
        assert report.coverage["nodes"]["missing"] == []
        # 真实策略面兑现的归因证据
        assert any("strategy=reassign" in note for note in report.notes)
    finally:
        engine.close()
