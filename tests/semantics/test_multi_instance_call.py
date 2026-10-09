"""多实例子流程（v2.3 第四批 A9，multi-instance-call-design.md——Zeebe 契约验收）。

- 三子全完：collect 按 index 对齐聚合（完成序无关）＋null 占位稳定；
- subject 注入：输入映射按子求值（每子拿自己的切片，子变量可证）；
- 空集合＝节点立即完成（查无传播→skip 语义，显式成功）；
- completion 提前收工：公式命中终止剩余 running 子；未命中等待；
- child_failed 形态：completed==total 一子失败即阻断；
- paused 子阻塞父（等待批语义在 MI 下延续）；
- validator：subjects 空 / merge+MI 被拦；
- 单子回归 + 模拟试跑递归。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.validator import InvalidDefinition
from flowork.engine.engine import ProcessEngine


def _deploy_child(engine: ProcessEngine, code: str = "mi_child") -> None:
    """子流程：单人任务（u_worker）——MI 的办理人是子定义的，subjects 只是切片。"""
    engine.repository.deploy(
        FlowBuilder(code)
        .start()
        .user_task("review", assignee="'u_worker'")
        .end()
        .connect("start", "review")
        .connect("review", "end")
        .build()
    )


def _mi_parent(
    engine: ProcessEngine, code: str, *, node_fields: dict | None = None, **mi_fields: object
) -> None:
    engine.repository.deploy(
        FlowBuilder(code)
        .start()
        .call_activity(
            "sub",
            process_code="mi_child",
            multi_instance={"subjects": ["'u_a'", "'u_b'", "'u_c'"], **mi_fields},
            **(node_fields or {}),
        )
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )


def _start(engine: ProcessEngine, code: str) -> str:
    return engine.runtime.start_process(code, start_user="u_starter")


def _children_of(engine: ProcessEngine, parent_id: str) -> list:
    return [i for i in engine.history.instances(include_simulated=True) if i.parent_id == parent_id]


def _complete_n(engine: ProcessEngine, count: int) -> None:
    """办完 count 个子流程任务（子任务全部指派给 u_worker）。"""
    for _ in range(count):
        task = engine.task.list_tasks(assignee="u_worker", state="ready")[0]
        engine.task.complete(task.id, actor="u_worker")


# —— 三子全完 + index 对齐 ——


def test_three_children_collect_aligned():
    engine = ProcessEngine()
    _deploy_child(engine)
    _mi_parent(
        engine,
        "p3",
        node_fields={"input_mappings": {"worker": "subject"}, "output_mappings": {"who": "worker"}},
    )
    instance_id = _start(engine, "p3")
    assert len(_children_of(engine, instance_id)) == 3

    _complete_n(engine, 2)
    assert engine.runtime.get_instance(instance_id).state == "running"  # 还差一子
    _complete_n(engine, 1)

    assert engine.runtime.get_instance(instance_id).state == "completed"
    # 输出按 subjects 序对齐（u_a/u_b/u_c 的位次＝subjects 顺序，与完成序无关）
    assert engine.runtime.variables_of(instance_id)["who"] == ["u_a", "u_b", "u_c"]


def test_collect_null_placeholder_when_child_failed():
    """失败子不产出：其位次保持 null（下标稳定），其余对齐。"""
    engine = ProcessEngine()
    _deploy_child(engine)
    _mi_parent(
        engine,
        "nullph",
        completion="child_completed + child_failed >= child_total",
        node_fields={
            "input_mappings": {"worker": "subject"},
            "output_mappings": {"who": "worker"},
        },
    )
    instance_id = _start(engine, "nullph")
    task = engine.task.list_tasks(assignee="u_worker", state="ready")[0]
    engine.runtime.terminate_process(task.instance_id, actor="admin")  # 一子失败
    _complete_n(engine, 2)

    assert engine.runtime.get_instance(instance_id).state == "completed"
    who = engine.runtime.variables_of(instance_id)["who"]
    assert who.count(None) == 1  # 失败位＝null 占位
    assert sorted(v for v in who if v) == ["u_b", "u_c"]


# —— subject 注入（每子拿自己的切片）——


def test_input_mapping_subject_slice():
    engine = ProcessEngine()
    _deploy_child(engine)
    _mi_parent(engine, "slice", node_fields={"input_mappings": {"worker": "subject"}})
    instance_id = _start(engine, "slice")
    values = {
        engine.runtime.variables_of(child.id).get("worker")
        for child in _children_of(engine, instance_id)
    }
    assert values == {"u_a", "u_b", "u_c"}


# —— 空集合＝立即完成（skip 语义）——


def test_lookup_miss_subjects_skip():
    """取人查无传播：prev_task 求值为空 → 零子实例 → 节点立即完成（Zeebe skip）。"""
    engine = ProcessEngine()
    _deploy_child(engine)
    engine.repository.deploy(
        FlowBuilder("miss")
        .start()
        .call_activity(
            "sub",
            process_code="mi_child",
            multi_instance={"subjects": ["prev_task.assignee"]},
        )
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    instance_id = _start(engine, "miss")  # 首节点前无 prev_task → 查无贡献空
    assert engine.runtime.get_instance(instance_id).state == "completed"
    assert _children_of(engine, instance_id) == []


# —— completion 提前收工 ——


def test_completion_formula_early_finish_terminates_rest():
    engine = ProcessEngine()
    _deploy_child(engine)
    _mi_parent(engine, "early", completion="child_completed >= 2")
    instance_id = _start(engine, "early")
    _complete_n(engine, 2)

    assert engine.runtime.get_instance(instance_id).state == "completed"  # 提前收工
    states = sorted(c.state for c in _children_of(engine, instance_id))
    assert states == ["cancelled", "completed", "completed"]  # 剩余 running 被终止
    assert engine.task.list_tasks(assignee="u_worker", state="ready") == []


def test_completion_not_hit_waits():
    engine = ProcessEngine()
    _deploy_child(engine)
    _mi_parent(engine, "wait2", completion="child_completed >= 2")
    instance_id = _start(engine, "wait2")
    _complete_n(engine, 1)
    assert engine.runtime.get_instance(instance_id).state == "running"


# —— child_failed 形态 ——


def test_strict_formula_blocks_on_failure():
    """completed==total：一子失败即不满足 → 父不完成（卡住等待管理员）。"""
    engine = ProcessEngine()
    _deploy_child(engine)
    _mi_parent(engine, "strict", completion="child_completed == child_total")
    instance_id = _start(engine, "strict")
    task = engine.task.list_tasks(assignee="u_worker", state="ready")[0]
    engine.runtime.terminate_process(task.instance_id, actor="admin")
    _complete_n(engine, 2)
    assert engine.runtime.get_instance(instance_id).state == "running"  # 1≠2 阻断


# —— paused 子阻塞父 ——


def test_paused_child_blocks_parent():
    engine = ProcessEngine()
    _deploy_child(engine)
    _mi_parent(engine, "susp")
    instance_id = _start(engine, "susp")
    task = engine.task.list_tasks(assignee="u_worker", state="ready")[0]
    engine.runtime.pause_process(task.instance_id, actor="admin")
    for _ in range(2):  # 其余两子办完（跳过暂停实例中的任务）
        runnable = next(
            t
            for t in engine.task.list_tasks(assignee="u_worker", state="ready")
            if engine.runtime.get_instance(t.instance_id).state == "running"
        )
        engine.task.complete(runnable.id, actor="u_worker")
    assert engine.runtime.get_instance(instance_id).state == "running"  # 暂停子阻塞


# —— validator ——


def test_validator_rejects_empty_subjects_and_merge():
    engine = ProcessEngine()
    _deploy_child(engine)
    with pytest.raises(InvalidDefinition, match="subjects_empty"):
        engine.repository.deploy(
            FlowBuilder("bad1")
            .start()
            .call_activity("sub", process_code="mi_child", multi_instance={"subjects": []})
            .end()
            .connect("start", "sub")
            .connect("sub", "end")
            .build()
        )

    engine2 = ProcessEngine()
    _deploy_child(engine2)
    with pytest.raises(InvalidDefinition, match="merge_forbidden"):
        engine2.repository.deploy(
            FlowBuilder("bad2")
            .start()
            .call_activity(
                "sub",
                process_code="mi_child",
                multi_instance={"subjects": ["'u_a'"], "output_mode": "merge"},
            )
            .end()
            .connect("start", "sub")
            .connect("sub", "end")
            .build()
        )


# —— 单子回归 + 模拟递归 ——


def test_single_child_regression_unchanged():
    engine = ProcessEngine()
    _deploy_child(engine)
    engine.repository.deploy(
        FlowBuilder("single")
        .start()
        .call_activity("sub", process_code="mi_child", output_mappings={"ok": "'yes'"})
        .end()
        .connect("start", "sub")
        .connect("sub", "end")
        .build()
    )
    instance_id = _start(engine, "single")
    _complete_n(engine, 1)
    assert engine.runtime.get_instance(instance_id).state == "completed"
    assert engine.runtime.variables_of(instance_id)["ok"] == "yes"  # merge 直写不变


def test_simulation_recurses_mi_children():
    engine = ProcessEngine()
    _deploy_child(engine)
    _mi_parent(engine, "simmi")
    report = engine.runtime.simulate("simmi")
    assert report.final_state == "completed"
    children = _children_of(engine, report.instance_id)
    assert len(children) == 3 and all(c.simulated for c in children)
