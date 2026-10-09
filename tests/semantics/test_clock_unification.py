"""时钟统一批（2026-10-09 第十四轮）：全表达式位 now 与 worker 租约恒取 EngineConfig.now。

来源：收口盘点——路由/取人/会签出口/超时改派/子流程映射/MI 汇聚各位点装配上下文时
未传 now（expression_context 缺省墙钟），worker Job 领活/退避三处直用 utc_now。
FakeClock 下被测对象与测试时钟不同源（route_audit 挂账「审计时刻与任务时刻不一致」
同根）。验收口径：每个位点一个「假时钟阈值」判例——阈值定在假钟 T0+1h（墙钟当下
早已越过），行为必须跟随引擎时钟而不是墙钟。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.node import MultiInstance, TaskDeadline
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine

# 假钟起点 2026-01-01T00:00Z，阈值 +1h（墙钟 2026-10 早已越过——判例的判别力所在）
GATE = "2026-01-01T01:00:00Z"


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, 0, 0, 0)

    def __call__(self) -> datetime:
        return self.value

    def advance_minutes(self, minutes: int) -> None:
        self.value += timedelta(minutes=minutes)


def build_engine() -> tuple[ProcessEngine, FakeClock]:
    clock = FakeClock()
    return ProcessEngine(EngineConfig(now=clock)), clock


def _open(engine: ProcessEngine, instance_id: str) -> list:
    return engine.task.list_tasks(instance_id=instance_id)


def _complete_first(engine: ProcessEngine, instance_id: str, actor: str) -> None:
    engine.task.complete(_open(engine, instance_id)[0].id, actor=actor)


# —— 路由条件位 ——


def test_route_condition_follows_engine_clock():
    engine, clock = build_engine()
    engine.repository.deploy(
        FlowBuilder("clk_route")
        .start()
        .user_task("work", assignee="'u_w'")
        .branch("gate")
        .user_task("late", assignee="'u_l'")
        .user_task("on_time", assignee="'u_o'")
        .end()
        .connect("start", "work")
        .connect("work", "gate")
        .flow("gate", "late", condition=f"now > timestamp('{GATE}')")
        .flow("gate", "on_time", default=True)
        .connect("late", "end")
        .connect("on_time", "end")
        .build()
    )
    # 假钟 T0 未过阈值 → on_time（墙钟早已过阈值，若路由读墙钟会走 late）
    iid = engine.runtime.start_process("clk_route", start_user="u_s")
    _complete_first(engine, iid, "u_w")
    assert [t.activity_code for t in _open(engine, iid)] == ["on_time"]

    # 假钟前拨 2h 过阈值 → late
    clock.advance_minutes(120)
    iid2 = engine.runtime.start_process("clk_route", start_user="u_s")
    _complete_first(engine, iid2, "u_w")
    assert [t.activity_code for t in _open(engine, iid2)] == ["late"]


def test_route_audit_snapshot_now_is_engine_clock():
    """route_audit 快照钉住的是引擎时钟（挂账「审计时刻与任务时刻不一致」收口）。"""
    engine, clock = build_engine()
    engine.repository.deploy(
        FlowBuilder("clk_audit")
        .start()
        .user_task("work", assignee="'u_w'")
        .branch("gate")
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "work")
        .connect("work", "gate")
        .flow("gate", "a", condition="now > timestamp('2000-01-01T00:00:00Z')")
        .flow("gate", "b", default=True)
        .connect("a", "end")
        .connect("b", "end")
        .build()
    )
    iid = engine.runtime.start_process("clk_audit", start_user="u_s")
    _complete_first(engine, iid, "u_w")
    gate_rows = [d for d in engine.history.route_decisions(iid) if d.source_activity == "gate"]
    assert gate_rows and gate_rows[0].snapshot["now"] == clock.value.isoformat()


# —— 取人位 ——


def test_assignee_expression_follows_engine_clock():
    engine, _clock = build_engine()
    engine.repository.deploy(
        FlowBuilder("clk_pick")
        .start()
        .user_task(
            "node",
            assignee=f"now > timestamp('{GATE}') ? 'u_late' : 'u_early'",
        )
        .end()
        .connect("start", "node")
        .connect("node", "end")
        .build()
    )
    iid = engine.runtime.start_process("clk_pick", start_user="u_s")
    assert [t.assignee for t in _open(engine, iid)] == ["u_early"]


# —— 会签出口位 ——


def test_countersign_exit_follows_engine_clock():
    engine, clock = build_engine()
    engine.repository.deploy(
        FlowBuilder("clk_cs")
        .start()
        .user_task(
            "sign",
            assignees=["'u1'", "'u2'", "'u3'"],
            multi_instance=MultiInstance(
                mode="parallel",
                approve_exit=f"now > timestamp('{GATE}') || task.approve_count >= task.item_count",
            ),
        )
        .end()
        .connect("start", "sign")
        .connect("sign", "end")
        .build()
    )
    iid = engine.runtime.start_process("clk_cs", start_user="u_s")
    _complete_first(engine, iid, "u1")  # 假钟未过阈值：时间条件不命中，2/3 不出口
    assert engine.runtime.get_instance(iid).state == "running"
    assert len(_open(engine, iid)) == 2

    clock.advance_minutes(120)
    _complete_first(engine, iid, "u2")  # 假钟过阈值：时间条件命中，2/3 提前出口
    assert engine.runtime.get_instance(iid).state == "completed"


# —— 超时改派位（fire 时求值 + task.* 快照）——


def test_reassign_expression_follows_engine_clock():
    engine, clock = build_engine()
    engine.repository.deploy(
        FlowBuilder("clk_reassign")
        .start()
        .user_task(
            "a",
            assignee="'u_a'",
            deadlines=[
                TaskDeadline(
                    after_minutes=15,
                    action="reassign",
                    reassign_to=f"now > timestamp('{GATE}') ? 'u_late' : 'u_early'",
                )
            ],
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    iid = engine.runtime.start_process("clk_reassign", start_user="u_s")
    clock.advance_minutes(20)  # 到期时刻 T0+20m，仍在阈值 T0+1h 之前
    engine.jobs.tick()
    assert [t.assignee for t in _open(engine, iid)] == ["u_early"]


# —— 子流程映射位 ——


def test_subprocess_input_mapping_follows_engine_clock():
    engine, _clock = build_engine()
    engine.repository.deploy(
        FlowBuilder("clk_child")
        .start()
        .user_task("w", assignee="'u_worker'")
        .end()
        .connect("start", "w")
        .connect("w", "end")
        .build()
    )
    engine.repository.deploy(
        FlowBuilder("clk_parent")
        .start()
        .call_activity(
            "sub",
            process_code="clk_child",
            # 不直存 now（CEL Timestamp 包装类型非 JSON 可序列化），取布尔派生值
            input_mappings={"past_gate": f"now > timestamp('{GATE}')"},
        )
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    iid = engine.runtime.start_process("clk_parent", start_user="u_s")
    (child,) = [i for i in engine.history.instances() if i.parent_id == iid]
    assert engine.runtime.variables_of(child.id)["past_gate"] is False


# —— 多实例汇聚位（completion 公式）——


def test_mi_completion_formula_follows_engine_clock():
    engine, clock = build_engine()
    engine.repository.deploy(
        FlowBuilder("mi_child_clk")
        .start()
        .user_task("review", assignee="'u_worker'")
        .end()
        .connect("start", "review")
        .connect("review", "end")
        .build()
    )
    engine.repository.deploy(
        FlowBuilder("clk_mi")
        .start()
        .call_activity(
            "sub",
            process_code="mi_child_clk",
            multi_instance={
                "subjects": ["'u_a'", "'u_b'", "'u_c'"],
                "completion": f"now > timestamp('{GATE}')",
            },
        )
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    iid = engine.runtime.start_process("clk_mi", start_user="u_s")
    children = [i for i in engine.history.instances() if i.parent_id == iid]
    assert len(children) == 3

    _complete_first(engine, children[0].id, "u_worker")  # 假钟未过阈值：不提前收工
    assert engine.runtime.get_instance(iid).state == "running"

    clock.advance_minutes(120)
    _complete_first(engine, children[1].id, "u_worker")  # 过阈值：提前收工，余子终止
    assert engine.runtime.get_instance(iid).state == "completed"
    states = {c.id: engine.runtime.get_instance(c.id).state for c in children}
    assert states[children[2].id] in ("cancelled", "terminated")


# —— worker Job 租约位 ——


def test_worker_lease_follows_engine_clock():
    engine, clock = build_engine()
    engine.repository.deploy(
        FlowBuilder("clk_idle")
        .start()
        .user_task("w", assignee="'u_w'")
        .end()
        .connect("start", "w")
        .connect("w", "end")
        .build()
    )
    iid = engine.runtime.start_process("clk_idle", start_user="u_s")
    job_id = engine.worker_jobs.create_worker_job(job_type="pull", instance_id=iid, payload={})

    (claim,) = engine.worker_jobs.activate_jobs(
        ["pull"], worker_id="w-1", visibility_timeout_seconds=3600
    )
    assert claim.job_id == job_id
    # 租约期内他人不可领（若租约按墙钟计算，T0+1h 早于墙钟当下，会被误判可重领）
    assert engine.worker_jobs.activate_jobs(["pull"], worker_id="w-2") == []

    clock.advance_minutes(120)  # 假钟越过租约 → 可重领，无需手工改 lease_until
    (reclaim,) = engine.worker_jobs.activate_jobs(
        ["pull"], worker_id="w-2", visibility_timeout_seconds=3600
    )
    assert reclaim.job_id == job_id
