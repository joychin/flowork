"""出口不可达兜底 + 裸门槛设计期提示 + 加签现时名单（spec v2.3 第二十轮·审计 D4/D3）。

- D4 运行期：user_task 全部待办办结而两侧出口均未达成 → 技术故障（挂起+
  故障单），拒绝静默卡死——场景 A（绝对门槛遇人数不足）与场景 B（缺省
  全票下混合票两侧均不达成）。
- D4 设计期：出口公式「task.<计数> 与裸数字比较」→ WARNING exit.bare_threshold。
- D3 条款化钉子：加签基名单＝加签时刻**现时**求值（非进入快照），变量变化
  即漂移——明文语义。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable
from flowork.definitions.node import MultiInstance
from flowork.definitions.validator import IssueLevel, validate_definition
from flowork.engine.engine import ProcessEngine
from flowork.engine.kernel import EngineExecutionError
from flowork.runtime.registry import create_registry


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def task_of(process_engine: ProcessEngine, instance_id: str, assignee: str):
    return process_engine.task.list_tasks(instance_id=instance_id, assignee=assignee)[0]


def _incidents(process_engine: ProcessEngine, instance_id: str) -> list[dict]:
    return process_engine.runtime.list_incidents(instance_id=instance_id)


# —— D4 运行期兜底 ——


def test_absolute_threshold_short_of_people_suspends_with_incident(engine: ProcessEngine):
    """场景 A：门槛 >=2 而只解析出 1 人——同意后待办清零、出口未达 → 挂起+故障单。"""
    definition = (
        FlowBuilder("d4a")
        .start()
        .user_task(
            "sign",
            assignees=["'u1'"],
            multi_instance=MultiInstance(mode="parallel", approve_exit="task.approve_count >= 2"),
        )
        .end()
        .connect("start", "sign")
        .connect("sign", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("d4a", start_user="u_s")

    with pytest.raises(EngineExecutionError, match="出口不可达"):
        engine.task.complete(task_of(engine, instance_id, "u1").id, actor="u1")
    assert engine.runtime.get_instance(instance_id).state == "suspended"
    incidents = _incidents(engine, instance_id)
    assert incidents and incidents[0]["state"] == "open"
    assert "item_count=1" in incidents[0]["message"]  # 三计数可读，运维可判


def test_mixed_votes_default_unanimity_suspends_with_incident(engine: ProcessEngine):
    """场景 B：3 人缺省全票，2 同意 1 否决——两侧均不达成，最后一票触发兜底。

    未达否决出口的反对票只计数不弹回（reject_flow 既有语义），三票投完
    待办清零而令牌未完——修复前这里是无声死寂。
    """
    definition = (
        FlowBuilder("d4b")
        .start()
        .user_task(
            "panel",
            assignees=["'u1'", "'u2'", "'u3'"],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "panel")
        .connect("panel", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("d4b", start_user="u_s")

    engine.task.complete(task_of(engine, instance_id, "u1").id, actor="u1")
    engine.task.complete(task_of(engine, instance_id, "u2").id, actor="u2")
    with pytest.raises(EngineExecutionError, match="出口不可达"):
        engine.task.reject(task_of(engine, instance_id, "u3").id, actor="u3")
    assert engine.runtime.get_instance(instance_id).state == "suspended"
    assert _incidents(engine, instance_id)


def test_normal_unanimity_and_or_exit_still_complete(engine: ProcessEngine):
    """对照组：全票同意与或签（>=1）不受兜底误伤。"""
    definition = (
        FlowBuilder("d4c")
        .start()
        .user_task(
            "all",
            assignees=["'u1'", "'u2'"],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .user_task(
            "any",
            assignees=["'u3'", "'u4'"],
            multi_instance=MultiInstance(mode="parallel", approve_exit="task.approve_count >= 1"),
        )
        .end()
        .connect("start", "all")
        .connect("all", "any")
        .connect("any", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process("d4c", start_user="u_s")
    engine.task.complete(task_of(engine, instance_id, "u1").id, actor="u1")
    engine.task.complete(task_of(engine, instance_id, "u2").id, actor="u2")
    engine.task.complete(task_of(engine, instance_id, "u3").id, actor="u3")
    assert engine.runtime.get_instance(instance_id).state == "completed"


# —— D4 设计期提示（exit.bare_threshold）——


def _warning_rules(definition: ProcessDefinition) -> set[str]:
    return {
        issue.rule
        for issue in validate_definition(definition, create_registry())
        if issue.level == IssueLevel.WARNING
    }


def test_bare_threshold_formula_warns():
    flow = (
        FlowBuilder("d4w1")
        .start()
        .user_task(
            "a",
            assignees=["'u1'", "'u2'"],
            multi_instance=MultiInstance(mode="parallel", approve_exit="task.approve_count >= 2"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    assert "exit.bare_threshold" in _warning_rules(flow.build())


def test_relative_formula_does_not_warn():
    flow = (
        FlowBuilder("d4w2")
        .start()
        .user_task(
            "a",
            assignees=["'u1'", "'u2'", "'u3'"],
            multi_instance=MultiInstance(
                mode="parallel",
                approve_exit="task.approve_count * 100 >= task.item_count * 60",
            ),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
    )
    assert "exit.bare_threshold" not in _warning_rules(flow.build())


# —— D3 加签现时名单（条款化钉子）——


def test_add_countersigner_resolves_current_roster(engine: ProcessEngine):
    """加签基名单＝加签时刻现时求值：变量改成 [u1,u3] 后加签 u9，
    item_count＝{u1,u3,u9}=3（u3 从未有待办却在分母、u2 的旧待办照办）——
    「现时名单为准」为明文语义（spec §5.1 第二十轮；快照语义列 v2.4 议题）。"""
    definition = (
        FlowBuilder("d3pin")
        .start()
        .user_task(
            "a",
            assignees=["approvers"],
            multi_instance=MultiInstance(mode="parallel"),
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    definition.variables = [ProcessVariable(name="approvers", type="list")]
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process(
        "d3pin", start_user="u_s", variables={"approvers": ["u1", "u2"]}
    )
    assert {t.assignee for t in engine.task.list_tasks(instance_id=instance_id)} == {"u1", "u2"}

    engine.task.complete(task_of(engine, instance_id, "u1").id, actor="u1")
    engine.runtime.update_variables(
        instance_id, {"approvers": ["u1", "u3"]}, actor="admin", reason="名单调整"
    )
    engine.task.add_countersigner(
        task_of(engine, instance_id, "u2").id, users=["u9"], actor="admin"
    )

    token = next(t for t in engine.history.tokens(instance_id) if t.activity_code == "a")
    assert token.item_count == 3  # 现时口径：基名单 [u1,u3] ∪ 手动 [] ∪ 新人 [u9]
    engine.task.complete(task_of(engine, instance_id, "u2").id, actor="u2")
    engine.task.complete(task_of(engine, instance_id, "u9").id, actor="u9")
    assert engine.runtime.get_instance(instance_id).state == "completed"
