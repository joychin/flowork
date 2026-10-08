"""幂等与版本：启动幂等三策略、任务完成幂等、deploy 即发布/内容哈希去重/破坏性判定。"""

from __future__ import annotations

import pytest

from flowork.engine.engine import ProcessEngine
from flowork.errors import DuplicateStartError, FloworkError
from tests.suite.features import covers
from tests.suite.walkthrough import task_of
from tests.suite.world_flows import EXPENSE_INPUT


def start_lifecycle(engine: ProcessEngine, **kwargs: object) -> str:
    return engine.runtime.start_process("suite_lifecycle", start_user="u_dev_wu", **kwargs)  # type: ignore[arg-type]


@covers("lifecycle.start_idempotency_reject")
def test_start_idempotency_reject_on_duplicate_key(suite_engine: ProcessEngine) -> None:
    first = start_lifecycle(suite_engine, idempotency_key="ord-1001")
    with pytest.raises(DuplicateStartError):
        start_lifecycle(suite_engine, idempotency_key="ord-1001")
    assert first


@covers("lifecycle.start_idempotency_return_existing")
def test_start_idempotency_return_existing(suite_engine: ProcessEngine) -> None:
    first = start_lifecycle(suite_engine, idempotency_key="ord-1002")
    again = start_lifecycle(
        suite_engine,
        idempotency_key="ord-1002",
        reuse="return_existing",  # type: ignore[arg-type]
    )
    assert again == first


@covers("lifecycle.start_idempotency_allow_duplicate")
def test_start_idempotency_allow_duplicate(suite_engine: ProcessEngine) -> None:
    first = start_lifecycle(suite_engine, idempotency_key="ord-1003")
    second = start_lifecycle(
        suite_engine,
        idempotency_key="ord-1003",
        reuse="allow_duplicate",  # type: ignore[arg-type]
    )
    assert second != first
    assert len(suite_engine.history.instances(definition_code="suite_lifecycle")) == 2


@covers("lifecycle.task_idempotency")
def test_task_complete_idempotent_replay(suite_engine: ProcessEngine) -> None:
    engine = suite_engine
    iid = start_lifecycle(engine)
    task_id = task_of(engine, iid, "u_dev_wu")

    first = engine.task.complete(task_id, actor="u_dev_wu", idempotency_key="submit-1")
    replay = engine.task.complete(task_id, actor="u_dev_wu", idempotency_key="submit-1")
    assert (replay.task_id, replay.outcome) == (first.task_id, first.outcome)

    rows = [row for row in engine.history.task_history(instance_id=iid) if row.task_id == task_id]
    assert len(rows) == 1, "重复提交不产生第二条已办"


@covers("version.content_hash_idempotent")
def test_deploy_identical_content_dedupes(suite_engine: ProcessEngine) -> None:
    from tests.suite.world_catalog import build_definition

    before = len(
        [e for e in suite_engine.history.events(limit=500) if e.event_name == "definition_deployed"]
    )
    verdict = suite_engine.repository.deploy(build_definition("suite_expense"))
    assert verdict.identical_to == (1, 1), "同内容哈希幂等：不落库不发新部署事件"
    after = len(
        [e for e in suite_engine.history.events(limit=500) if e.event_name == "definition_deployed"]
    )
    assert after == before


@covers("version.breaking_rules", "version.deploy_publish")
def test_deploy_breaking_change_bumps_major(suite_engine: ProcessEngine) -> None:
    from tests.suite.world_catalog import build_definition

    stricter = build_definition("suite_expense").model_copy(
        update={
            "input": {
                **EXPENSE_INPUT,
                "required": ["amount", "expense_type", "urgent"],  # R1：收紧 input 契约
            }
        }
    )
    verdict = suite_engine.repository.deploy(stricter)
    assert verdict.breaking
    assert verdict.next_major == 2 and verdict.next_revision == 1
    assert any("input" in rule.rule for rule in verdict.triggered_rules)

    # 破坏性变更后新实例默认钉最新版：新契约立即生效拒收缺urgent 的发起
    with pytest.raises(FloworkError, match="start"):
        suite_engine.runtime.start_process(
            "suite_expense",
            start_user="u_dev_wu",
            variables={"amount": 1, "expense_type": "office"},
        )
