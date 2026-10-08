"""F3 路由决策审计（expression-dataface §6/§9 F 组）：

重放解释（公式/结果/命中连线/取值快照可查）与事实一致性（快照票数与 token
重算一致）；查无跳过记 no_fact；无条件路由也记 selected。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.node import MultiInstance
from flowork.engine.engine import ProcessEngine

VOTE_GATE = 'node("voting").approve_count * 100 >= node("voting").item_count * 60'


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _open(engine: ProcessEngine, instance_id: str, activity: str | None = None) -> list:
    tasks = engine.task.list_tasks(instance_id=instance_id)
    return [t for t in tasks if activity is None or t.activity_code == activity]


def _decisions(engine: ProcessEngine, instance_id: str, source: str):
    return [d for d in engine.history.route_decisions(instance_id) if d.source_activity == source]


def test_route_decision_replay_for_voting_gate(engine: ProcessEngine):
    """F1 重放解释：招标主线——gate 决策行含公式/结果/命中连线/node() 取值快照。"""
    definition = (
        FlowBuilder("audit_tender")
        .start()
        .user_task(
            "voting",
            assignees=["'u_a'", "'u_b'", "'u_c'"],
            multi_instance=MultiInstance(mode="parallel", approve_exit="task.approve_count >= 2"),
        )
        .branch("gate")
        .user_task("committee", assignee="'u_dir'")
        .user_task("requote", assignee="'u_owner'")
        .end()
        .connect("start", "voting")
        .connect("voting", "gate")
        .flow("gate", "committee", condition=VOTE_GATE)
        .flow("gate", "requote", default=True)
        .connect("committee", "end")
        .connect("requote", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("audit_tender", start_user="u_s")
    for voter in ("u_a", "u_b"):
        engine.task.complete(_open(engine, iid, "voting")[0].id, actor=voter)
    assert [t.activity_code for t in _open(engine, iid)] == ["committee"]

    decisions = _decisions(engine, iid, "gate")
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.mode == "first_match"
    edge = decision.edges[0]
    assert edge == {"flow": "gate->committee", "formula": VOTE_GATE, "result": "true"}
    assert decision.selected == ["gate->committee"]
    # 取值快照：now + node() 读到的票数（与投票轮一致）
    assert "now" in decision.snapshot
    assert decision.snapshot["node"]["voting"]["approve_count"] == 2
    assert decision.snapshot["node"]["voting"]["item_count"] == 3


def test_snapshot_matches_token_recomputation(engine: ProcessEngine):
    """F2 事实一致：快照票数与 wf_token 现场重算一致（口径唯一性）。"""
    definition = (
        FlowBuilder("audit_consistency")
        .start()
        .user_task(
            "voting",
            assignees=["'u_a'", "'u_b'"],
            multi_instance=MultiInstance(mode="parallel", approve_exit="task.approve_count >= 2"),
        )
        .branch("gate")
        .user_task("yes", assignee="'u_y'")
        .user_task("no", assignee="'u_n'")
        .end()
        .connect("start", "voting")
        .connect("voting", "gate")
        .flow("gate", "yes", condition=VOTE_GATE)
        .flow("gate", "no", default=True)
        .connect("yes", "end")
        .connect("no", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("audit_consistency", start_user="u_s")
    for voter in ("u_a", "u_b"):
        engine.task.complete(_open(engine, iid, "voting")[0].id, actor=voter)

    decision = _decisions(engine, iid, "gate")[0]
    voting_tokens = [t for t in engine.history.tokens(iid) if t.activity_code == "voting"]
    latest = max(voting_tokens, key=lambda t: t.token_no)
    assert decision.snapshot["node"]["voting"]["approve_count"] == latest.approve_count
    assert decision.snapshot["node"]["voting"]["item_count"] == latest.item_count


def test_no_fact_skip_recorded(engine: ProcessEngine):
    """查无跳过记 no_fact（重放可解释「为什么没走这条边」），默认边记 selected。"""
    definition = (
        FlowBuilder("audit_no_fact")
        .start()
        .branch("gate")
        .user_task("big", assignee="'u_a'")
        .user_task("small", assignee="'u_b'")
        .end()
        .connect("start", "gate")
        .flow("gate", "big", condition="prev_task.outcome == 'agree'")
        .flow("gate", "small", default=True)
        .connect("big", "end")
        .connect("small", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("audit_no_fact", start_user="u_s")

    decision = _decisions(engine, iid, "gate")[0]
    assert decision.edges[0]["result"] == "no_fact"
    assert "prev_task" not in decision.snapshot  # 查无＝未注入
    assert decision.selected == ["gate->small"]


def test_unconditional_routing_records_selected_only(engine: ProcessEngine):
    """无条件路由（无公式）也落行：edges 空、selected 记激活连线。"""
    definition = (
        FlowBuilder("audit_plain")
        .start()
        .user_task("apply", assignee="'u_app'")
        .end()
        .connect("start", "apply")
        .connect("apply", "end")
        .build()
    )
    engine.repository.deploy(definition)
    iid = engine.runtime.start_process("audit_plain", start_user="u_s")

    start_decision = _decisions(engine, iid, "start")[0]
    assert start_decision.edges == []
    assert start_decision.selected == ["start->apply"]
    assert "now" in start_decision.snapshot

    # apply 完成后路由快照带 prev_task（上一站=apply 任务）
    engine.task.complete(_open(engine, iid, "apply")[0].id, actor="u_app")
    apply_exit = _decisions(engine, iid, "apply")[-1]
    assert apply_exit.edges == []
    assert apply_exit.selected == ["apply->end"]
    assert apply_exit.snapshot["prev_task"]["assignee"] == "u_app"
    assert apply_exit.snapshot["prev_task"]["outcome"] == "agree"
