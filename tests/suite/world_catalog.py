"""套件定义目录：code → 构建器 的单一事实源（fixture 部署与覆盖审计共用）。"""

from __future__ import annotations

from collections.abc import Callable

from flowork.definitions.definition import ProcessDefinition
from tests.suite.world_flows import (
    expense_definition,
    purchase_definition,
    supplier_check_definition,
)
from tests.suite.world_flows_branch import branch_matrix_definition
from tests.suite.world_flows_subprocess import (
    call_child_definition,
    call_grand_definition,
    call_matrix_definition,
)
from tests.suite.world_flows_time import (
    deadline_auto_definition,
    deadline_notify_definition,
    deadline_reassign_definition,
    deadline_reject_definition,
    fragile_definition,
    instance_deadline_definition,
    lifecycle_definition,
    message_definition,
)
from tests.suite.world_flows_wait import wait_duration_definition

BUILDERS: dict[str, Callable[[], ProcessDefinition]] = {
    "suite_expense": expense_definition,
    "suite_supplier_check": supplier_check_definition,
    "suite_purchase": purchase_definition,
    "suite_branch_matrix": branch_matrix_definition,
    "suite_wait_duration": wait_duration_definition,
    "suite_call_matrix": call_matrix_definition,
    "suite_call_child": call_child_definition,
    "suite_call_grand": call_grand_definition,
    "suite_deadline_notify": deadline_notify_definition,
    "suite_deadline_reassign": deadline_reassign_definition,
    "suite_deadline_auto": deadline_auto_definition,
    "suite_deadline_reject": deadline_reject_definition,
    "suite_deadline_mark": lambda: instance_deadline_definition("mark"),
    "suite_deadline_cancel": lambda: instance_deadline_definition("cancel"),
    "suite_deadline_terminate": lambda: instance_deadline_definition("terminate"),
    "suite_message": message_definition,
    "suite_lifecycle": lifecycle_definition,
    "suite_fragile": fragile_definition,
}

# 持久化目录世界（DirectoryStore）部署集：admin/委托 为占位，故不含 suite_purchase
STORE_WORLD_CODES = [
    "suite_expense",
    "suite_supplier_check",
    "suite_branch_matrix",
    "suite_wait_duration",
    "suite_call_matrix",
    "suite_call_child",
    "suite_call_grand",
    "suite_deadline_notify",
    "suite_deadline_reassign",
    "suite_deadline_auto",
    "suite_deadline_reject",
    "suite_deadline_mark",
    "suite_deadline_cancel",
    "suite_deadline_terminate",
    "suite_message",
    "suite_lifecycle",
    "suite_fragile",
]
# 静态目录世界（OrgDirectory：有 admins/委托）部署集
STATIC_WORLD_CODES = [
    "suite_expense",
    "suite_supplier_check",
    "suite_purchase",
    "suite_branch_matrix",
]


def build_definition(code: str) -> ProcessDefinition:
    return BUILDERS[code]()


def all_nodes() -> dict[str, tuple[str, ...]]:
    """全部世界定义的节点清单（覆盖审计基准）。"""
    result: dict[str, tuple[str, ...]] = {}
    for code in BUILDERS:
        definition = build_definition(code)
        result[code] = tuple(node.code for node in definition.nodes)
    return result
