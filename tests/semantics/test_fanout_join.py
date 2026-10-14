"""扇出两段式路由与 all 汇聚健全性（回归：测试套件完善批 3 随机结构发现）。

来源：tests/suite 随机结构生成 seed=20261013——all_match 分支的两条 cc 臂
（即时完结）在同一扇出命令内先后到达 all 汇聚：逐边即时处理时第二到达
时刻兄弟臂令牌尚不存在，可达性放行判定出现盲窗 → 汇聚被双激活，多出的
令牌无人收口（实例已完结仍挂 unfinished，违背悬空契约）。
修复＝route 两段式：先建齐扇出令牌、再逐个判完结。三钉子：
①双即时臂只产生一个汇聚令牌；②即时臂+人工臂混合时汇聚不提前放行；
③service 臂同族覆盖。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.engine.engine import ProcessEngine


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _fanout(arm_kinds: list[str]):
    """start → all_match 分流（两条件臂）→ all 汇聚 → end。臂内容按参数。"""

    def build():
        builder = FlowBuilder("fanout_join", "扇出汇聚").start()
        builder.branch("gate", "分流", route_mode="all_match")
        arm1, arm2 = "a_fast", "a_slow"
        if arm_kinds[0] == "cc":
            builder.cc_task(arm1, "传阅臂", recipients=["'u_a'"])
        else:
            builder.service_task(arm1, "服务臂", action="noop")
        if arm_kinds[1] == "cc":
            builder.cc_task(arm2, "传阅臂2", recipients=["'u_b'"])
        elif arm_kinds[1] == "service":
            builder.service_task(arm2, "服务臂2", action="noop")
        else:
            builder.user_task(arm2, "人工臂", assignee="'u_slow'")
        builder.user_task("merge", "汇聚签", assignee="'u_m'", advance_policy="all")
        builder.end("done", "结束")
        builder.flow("start", "gate")
        builder.flow("gate", arm1, condition="n >= 1")
        builder.flow("gate", arm2, condition="n >= 1")
        builder.flow(arm1, "merge")
        builder.flow(arm2, "merge")
        builder.flow("merge", "done")
        return builder.build().model_copy(
            update={"variables": [ProcessVariable(name="n", type="number", label="档", sample=1)]}
        )

    return build


def _drive_instant(engine: ProcessEngine, iid: str) -> None:
    task = engine.task.list_tasks(instance_id=iid, assignee="u_m")[0]
    engine.task.complete(task.id, actor="u_m")


def test_double_instant_arms_single_join_token(engine: ProcessEngine) -> None:
    """双即时臂（cc×cc）：汇聚只激活一次，完结后无幽灵令牌。"""
    engine.repository.deploy(_fanout(["cc", "cc"])())
    engine.register_handler("noop", lambda ctx, params: None)
    iid = engine.runtime.start_process("fanout_join", start_user="u_s", variables={"n": 1})
    _drive_instant(engine, iid)
    assert engine.runtime.get_instance(iid).state == "completed"
    merge_tokens = [t for t in engine.history.tokens(iid) if t.activity_code == "merge"]
    assert len(merge_tokens) == 1, "汇聚双激活（幽灵令牌）"
    assert all(
        t.state in ("finished", "cancelled") for t in engine.history.tokens(iid)
    ), "完结实例仍挂未完令牌"


def test_double_service_arms_single_join_token(engine: ProcessEngine) -> None:
    """双即时臂（service×service）：同族覆盖。"""
    engine.repository.deploy(_fanout(["service", "service"])())
    engine.register_handler("noop", lambda ctx, params: None)
    iid = engine.runtime.start_process("fanout_join", start_user="u_s", variables={"n": 1})
    _drive_instant(engine, iid)
    assert engine.runtime.get_instance(iid).state == "completed"
    merge_tokens = [t for t in engine.history.tokens(iid) if t.activity_code == "merge"]
    assert len(merge_tokens) == 1, "汇聚双激活（幽灵令牌）"


def test_instant_plus_human_arm_waits_for_human(engine: ProcessEngine) -> None:
    """即时臂+人工臂：汇聚不提前放行——人工臂未办完前不得出现汇聚待办。"""
    engine.repository.deploy(_fanout(["cc", "human"])())
    engine.register_handler("noop", lambda ctx, params: None)
    iid = engine.runtime.start_process("fanout_join", start_user="u_s", variables={"n": 1})
    active = {t.activity_code for t in engine.task.list_tasks(instance_id=iid)}
    assert active == {"a_slow"}, f"汇聚提前放行：{active}"
    engine.task.complete(
        engine.task.list_tasks(instance_id=iid, assignee="u_slow")[0].id, actor="u_slow"
    )
    _drive_instant(engine, iid)
    assert engine.runtime.get_instance(iid).state == "completed"
    merge_tokens = [t for t in engine.history.tokens(iid) if t.activity_code == "merge"]
    assert len(merge_tokens) == 1
