"""超时两目标的回归批（v2.3 第十四轮）：reassign_to / escalate_to 同款契约。

来源：2026-10-09 设计评审——escalate 原由引擎直接调 `org.manager_of`（全仓唯一
把「主管」概念编码进引擎的地方），非科层组织用不了。改为**图纸给目标**
（spec §5.5 分工铁律），引擎零组织假设。本文件钉住四件事：

- 设计/部署期：两目标都必填（缺 → ERROR，不拖到 fire 出故障单）；
- 运行期：两目标共用求值契约（fire 时求值、`task.*` 快照、变量可用），
  差别只在审计事件（escalate 记 TASK_ESCALATED，reassign 不记）；
- 无目录引擎：escalate 照常可用（目标纯来自图纸）；
- 目标不是人（如组引用）时运行期拒绝，不静默写进 assignee。
"""

from __future__ import annotations

import pytest

from flowork import contrib
from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable
from flowork.definitions.node import TaskDeadline
from flowork.definitions.validator import InvalidDefinition, IssueLevel, validate_definition
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.events.domain import DomainEvent
from flowork.participants.org_directory import OrgDirectory
from tests.suite.world_flows_time import FakeClock

# 科层小目录：u_a 的上级 u_boss；u_top 无上级（顶层）
ORG = OrgDirectory.from_dict(
    {
        "users": {"u_a": {"manager": "u_boss"}, "u_boss": {}, "u_top": {}},
        "departments": {},
    }
)

ESCALATE = "manager_of(task.assignee)"


def _one_deadline(code: str, action: str, **target: str) -> ProcessDefinition:
    definition = (
        FlowBuilder(code)
        .start()
        .user_task(
            "a",
            "限时",
            assignee="'u_a'",
            deadlines=[TaskDeadline(after_minutes=15, action=action, **target)],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    return definition


def _error_rules(engine: ProcessEngine, definition: ProcessDefinition) -> list[str]:
    issues = validate_definition(
        definition,
        engine.config.node_registry,
        expression_functions=engine.config.expression_functions,
    )
    return [i.rule for i in issues if i.level == IssueLevel.ERROR]


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine(
        EngineConfig(now=FakeClock(), org=ORG, expression_functions=contrib.cel_vocabulary(ORG))
    )


def test_escalate_without_target_is_design_time_error(engine: ProcessEngine) -> None:
    """escalate 缺 escalate_to → 设计期 ERROR（措辞给出科层写法与理由）。"""
    issues = validate_definition(
        _one_deadline("esc_missing", "escalate"),
        engine.config.node_registry,
        expression_functions=engine.config.expression_functions,
    )
    errors = [i for i in issues if i.level == IssueLevel.ERROR]
    assert [i.rule for i in errors] == ["user_task.deadline.escalate_to_missing"]
    assert "manager_of(task.assignee)" in errors[0].message
    assert "引擎不含组织模型" in errors[0].message


def test_escalate_with_target_deploys_but_missing_blocks(engine: ProcessEngine) -> None:
    """带目标 → 零 ERROR 可发布；缺目标 → deploy 拒绝（InvalidDefinition）。"""
    ok = _one_deadline("esc_ok", "escalate", escalate_to=ESCALATE)
    assert _error_rules(engine, ok) == []
    engine.repository.deploy(ok)

    with pytest.raises(InvalidDefinition):
        engine.repository.deploy(_one_deadline("esc_bad", "escalate"))


def test_reassign_target_rule_name_unchanged(engine: ProcessEngine) -> None:
    """reassign 的既有规则名保持 `reassign_to_missing`（零兼容破坏）。"""
    assert _error_rules(engine, _one_deadline("re_missing", "reassign")) == [
        "user_task.deadline.reassign_to_missing"
    ]


def test_escalate_target_is_statically_checked(engine: ProcessEngine) -> None:
    """escalate_to 是完整表达式位：未注册函数设计期即拦（与 reassign 同位点）。

    注：引用/函数检查只在**声明了流程变量**时启用（spec §3.2 声明不约束），
    故这里补一条声明——与连线位的静态检查行为一致。
    """
    bad = _one_deadline("esc_static", "escalate", escalate_to="nosuchfn(task.assignee)")
    bad.variables = [ProcessVariable(name="amount", type="number")]
    assert "expression.function.unknown" in _error_rules(engine, bad)


def test_escalate_evaluates_with_task_snapshot(engine: ProcessEngine) -> None:
    """运行期：目标表达式拿到 `task.*` 快照（当前办理人 u_a 的上级 u_boss）。"""
    definition = (
        FlowBuilder("esc_snapshot")
        .start()
        .user_task(
            "a",
            "限时",
            assignee="'u_a'",
            deadlines=[TaskDeadline(after_minutes=15, action="escalate", escalate_to=ESCALATE)],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("esc_snapshot", start_user="u_s")
    clock: FakeClock = engine.config.now  # type: ignore[assignment]
    clock.advance(15)
    engine.jobs.tick()

    assert [t.assignee for t in engine.task.list_tasks(instance_id=iid)] == ["u_boss"]
    events = [e.event_name for e in engine.history.events(instance_id=iid, limit=50)]
    assert DomainEvent.TASK_ESCALATED.value in events, "escalate 记升级事件（reassign 不记）"


def test_escalate_target_resolving_empty_raises_incident(engine: ProcessEngine) -> None:
    """顶层（无上级）：表达式求值出 None → 故障单（不静默丢，spec §5.1）。"""
    definition = (
        FlowBuilder("esc_top")
        .start()
        .user_task(
            "b",
            "限时",
            assignee="'u_top'",
            deadlines=[TaskDeadline(after_minutes=15, action="escalate", escalate_to=ESCALATE)],
        )
        .end()
        .connect("start", "b")
        .connect("b", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("esc_top", start_user="u_s")
    clock: FakeClock = engine.config.now  # type: ignore[assignment]
    clock.advance(15)
    engine.jobs.tick()

    incidents = [i for i in engine.runtime.list_incidents() if i["instance_id"] == iid]
    assert incidents and incidents[0]["state"] == "open"
    assert [t.assignee for t in engine.task.list_tasks(instance_id=iid)] == ["u_top"], "任务未动"


def test_escalate_usable_without_any_directory() -> None:
    """无目录引擎（EngineConfig.org 未配）：escalate 照常可用——目标来自图纸/变量。"""
    bare = ProcessEngine(EngineConfig(now=FakeClock()))
    definition = (
        FlowBuilder("esc_nodir")
        .start()
        .user_task(
            "a",
            "限时",
            assignee="'u_a'",
            deadlines=[
                TaskDeadline(after_minutes=15, action="escalate", escalate_to="escalate_to_user")
            ],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    definition.variables = [ProcessVariable(name="escalate_to_user", type="string", label="升级给")]
    bare.repository.deploy(definition)

    iid = bare.runtime.start_process(
        "esc_nodir", start_user="u_s", variables={"escalate_to_user": "u_lead"}
    )
    clock: FakeClock = bare.config.now  # type: ignore[assignment]
    clock.advance(15)
    bare.jobs.tick()
    assert [t.assignee for t in bare.task.list_tasks(instance_id=iid)] == ["u_lead"]
