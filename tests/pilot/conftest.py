"""试点共享夹具：组织目录 + 五个模板（报销/请假/采购/合同/供应商合规）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowork import contrib
from flowork.definitions.definition import load_definition
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.participants.org_directory import OrgDirectory

PILOT_DIR = Path(__file__).parent.parent.parent / "examples" / "pilot"
TEMPLATES = [
    "expense_claim",
    "leave_request",
    "supplier_compliance",
    "purchase_order",
    "contract_review",
]


@pytest.fixture
def pilot() -> ProcessEngine:
    """带试点组织目录的引擎，五个模板均已发布。"""
    engine = ProcessEngine(
        EngineConfig(
            org=OrgDirectory.from_yaml(PILOT_DIR / "org.yaml"),
            participant_functions=contrib.org_vocabulary(),  # v2.1：词汇由宿主挂
        )
    )
    for code in TEMPLATES:
        definition = load_definition(
            (PILOT_DIR / f"{code}.json").read_text(encoding="utf-8"), engine.config.node_registry
        )
        engine.repository.deploy(definition)

    def make_payment(ctx, params):
        ctx.variables["paid"] = True
        ctx.variables["paid_amount"] = ctx.variables.get("amount")
        ctx.emit("payment_executed", {"amount": ctx.variables.get("amount")})

    engine.register_handler("make_payment", make_payment)
    return engine


def complete_of(engine: ProcessEngine, instance_id: str, assignee: str, **kwargs) -> object:
    """办理指定人的当前待办（常用断言由调用方做）。"""
    tasks = engine.task.list_tasks(instance_id=instance_id, assignee=assignee)
    assert tasks, f"{assignee} 无待办"
    return engine.task.complete(tasks[0].id, actor=assignee, **kwargs)


def reject_of(engine: ProcessEngine, instance_id: str, assignee: str, **kwargs) -> object:
    tasks = engine.task.list_tasks(instance_id=instance_id, assignee=assignee)
    assert tasks, f"{assignee} 无待办"
    return engine.task.reject(tasks[0].id, actor=assignee, **kwargs)


def activities_of(engine: ProcessEngine, instance_id: str) -> list[tuple[str, str]]:
    """当前待办 (activity, assignee) 列表。"""
    return [(t.activity_code, t.assignee) for t in engine.task.list_tasks(instance_id=instance_id)]
