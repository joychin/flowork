"""回归：手册反向 review 修复批（docs_research/手册反向review-2026-10.md）。

- 未注册 service handler 归一 EngineExecutionError（挂起+故障单）——此前裸
  UnknownHandler 冒泡只回滚不留痕，与部署期 WARNING 文案「运行期解析不到
  将出故障单」互相矛盾；
- input 契约保留词设计期拦截（同名会遮蔽引擎注入值，实测 initiator 可被
  变量袋覆盖）；
- target_major 指向无已发布版本的 major 显式报错（此前静默落全局最高版本）；
- 旧形态 timeout 的失效策略设计期 WARNING（此前装载得过、超时静默失效）。
"""

from __future__ import annotations

import pytest

from flowork.api import FlowBuilder, ProcessEngine
from flowork.definitions.validator import (
    InvalidDefinition,
    IssueLevel,
    validate_definition,
)
from flowork.engine.kernel import EngineExecutionError
from flowork.errors import FloworkError


def _rules_of(engine: ProcessEngine, definition: object) -> set[tuple[str, IssueLevel]]:
    return {
        (issue.rule, issue.level)
        for issue in validate_definition(definition, engine.config.node_registry)
    }


def _flow(code: str, name: str, **task_kwargs: object) -> object:
    return (
        FlowBuilder(code, name)
        .start()
        .user_task("t1", assignee="'u1'", **task_kwargs)
        .end("fin")
        .connect("start", "t1")
        .connect("t1", "fin")
        .build()
    )


# ---------- C-1 未注册 handler ＝ 技术故障（挂起+故障单） ----------


def test_unregistered_service_handler_becomes_incident():
    engine = ProcessEngine()
    spec = (
        FlowBuilder("mrv_ghost")
        .start()
        .user_task("t1", assignee="'u1'")
        .service_task("svc", action="ghost")
        .end("fin")
        .connect("start", "t1")
        .connect("t1", "svc")
        .connect("svc", "fin")
        .build()
    )
    engine.repository.deploy(spec)  # 部署期仅 WARNING（先部署后注册合法）
    iid = engine.runtime.start_process("mrv_ghost", start_user="u0")
    task = engine.task.list_tasks(instance_id=iid)[0]
    with pytest.raises(EngineExecutionError):
        engine.task.complete(task.id, actor="u1")
    instance = engine.runtime.get_instance(iid)
    assert instance.state == "suspended"
    incidents = engine.runtime.list_incidents(iid)
    assert len(incidents) == 1
    assert incidents[0]["error_class"] == "EngineExecutionError"
    # 命令回滚：待办原样保留
    assert len(engine.task.list_tasks(instance_id=iid)) == 1


def test_empty_action_service_task_still_noop():
    engine = ProcessEngine()
    spec = (
        FlowBuilder("mrv_noop")
        .start()
        .user_task("t1", assignee="'u1'")
        .service_task("svc")
        .end("fin")
        .connect("start", "t1")
        .connect("t1", "svc")
        .connect("svc", "fin")
        .build()
    )
    engine.repository.deploy(spec)
    iid = engine.runtime.start_process("mrv_noop", start_user="u0")
    task = engine.task.list_tasks(instance_id=iid)[0]
    engine.task.complete(task.id, actor="u1")
    assert engine.runtime.get_instance(iid).state == "completed"
    assert engine.runtime.list_incidents(iid) == []


# ---------- C-2 input 契约保留词设计期拦截 ----------


def test_input_contract_reserved_property_rejected():
    engine = ProcessEngine()
    spec = _flow("mrv_res", "保留词").model_copy(
        update={
            "input": {
                "type": "object",
                "properties": {"now": {"type": "number"}, "order_id": {"type": "string"}},
            }
        }
    )
    assert ("input.reserved_name", IssueLevel.ERROR) in _rules_of(engine, spec)
    with pytest.raises(InvalidDefinition):
        engine.repository.deploy(spec)


def test_input_contract_normal_property_still_passes():
    engine = ProcessEngine()
    spec = _flow("mrv_res_ok", "正常契约").model_copy(
        update={
            "input": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
            }
        }
    )
    assert not any(rule == "input.reserved_name" for rule, _ in _rules_of(engine, spec))
    engine.repository.deploy(spec)


# ---------- C-3 target_major 须指向已发布过的 major ----------


def test_target_major_without_published_versions_rejected():
    engine = ProcessEngine()
    engine.repository.deploy(_flow("mrv_tm", "A"))  # (1, 1)
    with pytest.raises(FloworkError, match="target_major=3"):
        engine.repository.deploy(_flow("mrv_tm", "B"), target_major=3)
    with pytest.raises(FloworkError, match="target_major=3"):
        engine.repository.verdict(_flow("mrv_tm", "B"), target_major=3)
    # 正常维护分支不受影响
    v = engine.repository.deploy(_flow("mrv_tm", "C"), target_major=1)
    assert (v.next_major, v.next_revision) == (1, 2)


def test_retire_recovery_unaffected_by_target_major_guard():
    engine = ProcessEngine()
    engine.repository.deploy(_flow("mrv_ret", "A"))
    engine.repository.retire("mrv_ret", actor="admin", reason="下线")
    v = engine.repository.deploy(_flow("mrv_ret", "B"))
    assert (v.next_major, v.next_revision) == (1, 2)  # 全 retired 恢复＝越过最高续走


# ---------- C-8 旧形态 timeout 失效策略设计期 WARNING ----------


def test_legacy_timeout_ineffective_strategy_warns():
    engine = ProcessEngine()
    spec = _flow("mrv_to", "旧超时", timeout={"due_minutes": 10, "strategy": "cancel"})
    assert (
        "user_task.timeout.strategy_ineffective",
        IssueLevel.WARNING,
    ) in _rules_of(engine, spec)
    engine.repository.deploy(spec)  # WARNING 不拦发布


def test_legacy_timeout_valid_strategy_no_warning():
    engine = ProcessEngine()
    spec = _flow("mrv_to_ok", "旧超时有效", timeout={"due_minutes": 10, "strategy": "notify"})
    assert not any(
        rule == "user_task.timeout.strategy_ineffective" for rule, _ in _rules_of(engine, spec)
    )
