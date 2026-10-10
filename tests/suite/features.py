"""特性矩阵（单一事实源）+ 覆盖登记装饰器。

@covers("category.feature") 标注测试覆盖的特性；@covers_node("code", "node") 标注
走查到的节点。审计测试（test_coverage_audit.py）用 ast 静态扫描装饰器实参，
比对 FEATURES 全集与 world_catalog.all_nodes() 全集，缺一即红灯。
矩阵覆盖 tests/suite 与 tests/pilot 两层走查（审计扫描两目录）。
"""

from __future__ import annotations

from collections.abc import Callable

FEATURES: dict[str, tuple[str, ...]] = {
    "node": (
        "node.start",
        "node.end",
        "node.user_task",
        "node.service_task",
        "node.call_activity",
        "node.cc_task",
        "node.branch",
        "node.wait",
    ),
    "subprocess": (
        "subprocess.input_mapping",
        "subprocess.output_mapping",
        "subprocess.child_reject",
        "subprocess.child_recover",
        "subprocess.version_snapshot",
    ),
    "route": (
        "route.first_match",
        "route.all_match",
        "route.default_edge",
        "route.formula",
        "route.parallel_fork",
        "join.any_absorb",
        "join.all_gate",
        # —— 混用世界族（连线规则 × 找人 × 环境变量）——
        "route.fact_gate",
        "route.mixed_formula",
    ),
    "countersign": (
        "countersign.parallel",
        "countersign.sequential",
        "countersign.approve_all",
        "countersign.approve_any",
        "countersign.approve_ratio",
        "countersign.disapprove_all_default",
        "countersign.auto_pass_remaining",
    ),
    "reject": (
        "reject.explicit_target",
        "reject.node_target",
        "reject.to_start",
        "reject.source_fallback",
        "reject.cause_token",
        "reject.cancel_sibling_tokens",
    ),
    "ops": (
        "ops.claim",
        "ops.unclaim",
        "ops.transfer",
        "ops.assist",
        "ops.complete_assist",
        "ops.add_countersigner",
        "ops.withdraw",
        "ops.cc_read",
    ),
    "participant": (
        "participant.assignee_literal",
        "participant.assignees_list",
        "participant.expression_manager_of",
        "participant.expression_users_with_role",
        "participant.originator",
        "participant.candidate_users",
        "participant.candidate_groups",
        "participant.delegation",
        "participant.auto_pass_originator",
        "participant.no_participant_to_admins",
        # —— 混用世界族（找人 × CEL × 环境变量）——
        "participant.expression_mixed",
        "participant.expression_fact_chain",
        "participant.host_functions",
        "participant.null_spread",
    ),
    "lifecycle": (
        "lifecycle.start_idempotency_reject",
        "lifecycle.start_idempotency_return_existing",
        "lifecycle.start_idempotency_allow_duplicate",
        "lifecycle.task_idempotency",
        "lifecycle.pause_resume",
        "lifecycle.terminate",
        "lifecycle.cancel",
    ),
    "deadline": (
        "deadline.notify",
        "deadline.reassign",
        "deadline.escalate",
        "deadline.auto_approve",
        "deadline.auto_reject",
        "deadline.instance_mark",
        "deadline.instance_cancel",
        "deadline.instance_terminate",
        "deadline.due_at",
    ),
    "contract": (
        "contract.start_boundary",
        "contract.task_output_boundary",
        "contract.node_output_boundary",
        "contract.variable_declarations",
        "contract.variable_index_search",
    ),
    "variables": (
        "variables.external_update",
        "variables.reroute_pending_branch",
        "variables.terminal_guard",
        "variables.history",
    ),
    "query": (
        "query.instance_states",
        "query.list_tasks",
        "query.paged_tasks",
        "query.task_history_append_only",
        "query.events_stream",
        "query.tokens",
        "query.trace_instance",
        "query.route_decisions",
        "query.anticipate",
        "query.cc_list",
        "query.archive_instance",
    ),
    "org": (
        "org.unit_crud",
        "org.move_cycle_guard",
        "org.delete_protection",
        "org.scoped_role_resolution",
        "org.role_assign",
        "org.is_member_groups",
        "org.snapshot",
        "org.manager_reroute",
        # —— 混用世界族与端口口径（2026-10-09 第十四轮）——
        "org.manager_of_parity",
    ),
    "message": (
        "message.buffered_first",
        "message.correlate",
        "message.dedup",
        "message.signal_broadcast",
    ),
    "fault": (
        "fault.technical_suspends",
        "fault.business_no_incident",
        "fault.resolve_resume",
        "fault.resolve_retry",
        "fault.resolve_skip",
        "fault.resolve_cancel",
        "fault.resolve_fail",
        "fault.admin_move_token",
    ),
    "version": (
        "version.deploy_publish",
        "version.content_hash_idempotent",
        "version.breaking_rules",
        "version.instance_snapshot",
    ),
}


def all_features() -> set[str]:
    return {name for group in FEATURES.values() for name in group}


def covers(*features: str) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """登记测试覆盖的特性（运行时恒等装饰器；审计走 ast 静态扫描）。"""

    def decorator(fn: Callable[..., object]) -> Callable[..., object]:
        fn.__covered_features__ = features
        return fn

    return decorator


def covers_node(
    definition_code: str, *node_codes: str
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """登记测试走查到的世界定义节点（审计比对 all_nodes 全集）。"""

    def decorator(fn: Callable[..., object]) -> Callable[..., object]:
        existing = getattr(fn, "__covered_nodes__", ())
        fn.__covered_nodes__ = existing + tuple((definition_code, code) for code in node_codes)
        return fn

    return decorator
