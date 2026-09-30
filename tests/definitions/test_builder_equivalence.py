"""builder 与 JSON 等价性测试：同一流程两种写法产出相同定义（边界 M0 DoD）。"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.builtin import GatewayKind
from flowork.definitions.definition import load_definition_file
from flowork.definitions.flow import FlowCondition
from flowork.definitions.node import MultiInstance


def test_builder_matches_json_simple(registry, examples_dir):
    definition = load_definition_file(examples_dir / "01_simple_approval.json", registry)
    built = (
        FlowBuilder("simple_approval", "串行审批（示例 01）")
        .start("start", "发起")
        .user_task("manager", "经理审批", assignee="u_manager")
        .user_task("finance", "财务复核", assignee="u_finance")
        .end("end", "结束")
        .connect("start", "manager")
        .connect("manager", "finance")
        .connect("finance", "end")
        .build(registry, version=1, status="published")
    )
    assert built == definition


def test_builder_matches_json_countersign_branch(registry, examples_dir):
    definition = load_definition_file(examples_dir / "02_countersign_branch.json", registry)
    high_amount = FlowCondition(groups=[[{"left": "amount", "operator": "gt", "right": 10000}]])
    built = (
        FlowBuilder("countersign_branch", "条件分支 + 会签（示例 02）")
        .start("start", "发起")
        .user_task("manager", "经理审批", assignee="u_manager")
        .gateway("route", "按金额分流", kind=GatewayKind.EXCLUSIVE)
        .user_task(
            "director",
            "总监会签",
            assignees=["u_alice", "u_bob"],
            multi_instance=MultiInstance(mode="parallel", approve_exit=">=50%"),
            reject_target="manager",
        )
        .cc_task("notify", "结果传阅", recipients=["u_originator"])
        .end("end", "结束")
        .connect("start", "manager")
        .connect("manager", "route")
        .flow("route", "director", condition=high_amount)
        .flow("route", "notify", default=True)
        .connect("director", "end")
        .connect("notify", "end")
        .build(registry, version=1, status="published")
    )
    assert built == definition
