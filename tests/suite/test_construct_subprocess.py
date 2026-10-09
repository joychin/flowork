"""子流程构造族走查：call_activity 的八个维度（映射/嵌套/多子/驳回/暂停/版本/终止/默认臂）。

暂停钉子的诚实注记：当前唤醒机制下，父侧 CallActivity 令牌只在**其子实例完成**时
被唤醒重评，暂停中的子实例本就不会触发唤醒——本组测试把「暂停子阻塞父侧收口」
钉为行为语义；若未来唤醒机制改变（如父侧在任何子事件上重评），
sub_process_finished 的结算判定（_CHILD_SETTLED_STATES）保证不回归。
父 terminate 级联子实例（第十四轮收口，terminate_cascade.py）：原「孤儿运行」
开放问题已修——未结算子实例（含暂停/挂起中的）随父同置终态，待办撤销留痕。
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers, covers_node
from tests.suite.invariants import assert_no_dangling
from tests.suite.walkthrough import complete_of, reject_of


def _children_of(engine: ProcessEngine, parent_id: str) -> list:
    return [i for i in engine.history.instances() if i.parent_id == parent_id]


def _start_matrix(engine: ProcessEngine, mode: str, child_note: str = "n1") -> str:
    return engine.runtime.start_process(
        "suite_call_matrix",
        start_user="u_dev_wu",
        variables={"mode": mode, "child_note": child_note, "overwrite_me": "old"},
    )


@covers("subprocess.input_mapping")
@covers_node("suite_call_child", "start", "middle", "child_end")
@covers_node("suite_call_matrix", "start", "hub", "call_nested", "pay", "matrix_end")
def test_input_mapping_dimensions(suite_engine: ProcessEngine) -> None:
    """输入映射三形态：同名直传 / 字面量 / 缺键裸标识（→ None）。"""
    iid = _start_matrix(suite_engine, "nested")
    (child,) = _children_of(suite_engine, iid)
    child_vars = suite_engine.runtime.variables_of(child.id)
    assert child_vars["child_note"] == "n1"  # 同名直传
    assert child_vars["literal_note"] == "字面量"  # 字面量表达式
    assert child_vars["ghost"] is None  # 缺键裸标识 → None（数据缺失≠求值错）


@covers("subprocess.output_mapping")
@covers_node("suite_call_grand", "start", "leaf", "grand_end")
@covers_node("suite_call_child", "down")
def test_output_mapping_overwrites_parent(suite_engine: ProcessEngine) -> None:
    """输出映射：多键写回 + 孙辈值两层穿透 + 覆盖父已有变量。"""
    iid = _start_matrix(suite_engine, "nested")
    (child,) = _children_of(suite_engine, iid)
    complete_of(suite_engine, child.id, "u_cash")  # middle → down 激活孙
    (grand,) = _children_of(suite_engine, child.id)
    complete_of(suite_engine, grand.id, "u_compliance")  # leaf → 孙完结，子收口
    assert suite_engine.runtime.get_instance(child.id).state == "completed"
    assert suite_engine.runtime.get_instance(iid).state == "completed"

    variables = suite_engine.runtime.variables_of(iid)
    assert variables["from_child"] == "n1"  # 孙的 grand_note 经子 from_grand 两层回传
    assert variables["extra"] == "n1"  # 子的 child_note 直取
    assert variables["overwrite_me"] == "n1"  # 覆盖父已有变量（原 old）
    assert variables.get("erped") is True  # pay 收口动作已执行（echo_paid 写 erped）
    assert_no_dangling(suite_engine)


@covers("node.call_activity")
@covers_node("suite_call_matrix", "call_direct")
def test_two_children_parallel_join(suite_engine: ProcessEngine) -> None:
    """多子并行：双臂各调一个子实例，全部结算后 pay 等齐收口。"""
    iid = _start_matrix(suite_engine, "both")
    children = _children_of(suite_engine, iid)
    assert {c.definition_code for c in children} == {"suite_call_child", "suite_call_grand"}
    by_code = {c.definition_code: c.id for c in children}

    # 嵌套臂：孙先完结，子才收口
    complete_of(suite_engine, by_code["suite_call_child"], "u_cash")
    (grandchild,) = _children_of(suite_engine, by_code["suite_call_child"])
    complete_of(suite_engine, grandchild.id, "u_compliance")
    assert suite_engine.runtime.get_instance(by_code["suite_call_child"]).state == "completed"

    # 直调臂独立完结
    complete_of(suite_engine, by_code["suite_call_grand"], "u_compliance")
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(suite_engine)


@covers("route.all_match", "route.default_edge")
@covers_node("suite_call_matrix", "skipped_direct")
def test_default_arm_when_no_condition_matches(suite_engine: ProcessEngine) -> None:
    """all_match 全不命中 → 默认臂独走（无子流程启动）。"""
    iid = _start_matrix(suite_engine, "other")
    assert _children_of(suite_engine, iid) == []
    complete_of(suite_engine, iid, "u_chief_acc")
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(suite_engine)


@covers("subprocess.child_reject")
def test_child_reject_restarts_child_then_parent_proceeds(suite_engine: ProcessEngine) -> None:
    """子内驳回到子 start：子内重报后再通过，父侧照常收口。"""
    iid = _start_matrix(suite_engine, "nested")
    (child,) = _children_of(suite_engine, iid)
    reject_of(suite_engine, child.id, "u_cash", comment="材料不齐")
    assert suite_engine.runtime.get_instance(child.id).state == "running"  # 子内回 start

    start_task = suite_engine.task.list_tasks(instance_id=child.id, assignee="u_dev_wu")[0]
    suite_engine.task.complete(start_task.id, actor="u_dev_wu", variables={"child_note": "n2"})
    complete_of(suite_engine, child.id, "u_cash")
    (grand,) = _children_of(suite_engine, child.id)
    complete_of(suite_engine, grand.id, "u_compliance")

    assert suite_engine.runtime.get_instance(child.id).state == "completed"
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    # 驳回后重报的输入映射随子变量更新：from_child 取到新值
    assert suite_engine.runtime.variables_of(iid)["from_child"] == "n2"
    assert_no_dangling(suite_engine)


@covers("lifecycle.pause_resume")
def test_paused_child_blocks_parent_settlement(suite_engine: ProcessEngine) -> None:
    """暂停钉子：暂停中的子实例阻塞父侧——其余臂完成也不收口，恢复办结后父才完。

    回归背景：sub_process_finished 原先只挡 running，paused/suspended 子被当
    已结算（与「全部完结」docstring 矛盾）；现按终态集合判定（2026-10-09 修）。
    """
    iid = _start_matrix(suite_engine, "both")
    children = {c.definition_code: c.id for c in _children_of(suite_engine, iid)}
    suite_engine.runtime.pause_process(children["suite_call_grand"], actor="admin")

    # 嵌套臂全部办结
    complete_of(suite_engine, children["suite_call_child"], "u_cash")
    (grandchild,) = _children_of(suite_engine, children["suite_call_child"])
    complete_of(suite_engine, grandchild.id, "u_compliance")

    # 暂停臂未结算：父不得收口
    assert suite_engine.runtime.get_instance(iid).state == "running"

    suite_engine.runtime.resume_process(children["suite_call_grand"], actor="admin")
    complete_of(suite_engine, children["suite_call_grand"], "u_compliance")
    assert suite_engine.runtime.get_instance(iid).state == "completed"
    assert_no_dangling(suite_engine)


@covers("subprocess.version_snapshot", "version.instance_snapshot")
def test_child_version_snapshot(suite_engine: ProcessEngine) -> None:
    """子定义版本快照：在途孙实例按启动时版本跑完，升级只影响新实例。"""
    iid = _start_matrix(suite_engine, "nested")
    (child,) = _children_of(suite_engine, iid)
    complete_of(suite_engine, child.id, "u_cash")
    (grand,) = _children_of(suite_engine, child.id)
    assert grand.definition_revision == 1

    # 发布孙 v2：leaf 改名（revision 级变更）
    revised = (
        FlowBuilder("suite_call_grand", "套件·孙流程 v2")
        .start()
        .user_task("leaf", "孙辈办结（v2）", assignee="'u_compliance'", reject_target="start")
        .end("grand_end", "孙流程结束")
        .flow("start", "leaf")
        .flow("leaf", "grand_end")
        .build()
    )
    suite_engine.repository.deploy(revised)

    # 在途孙按 v1 快照跑完
    complete_of(suite_engine, grand.id, "u_compliance")
    assert suite_engine.runtime.get_instance(grand.id).definition_revision == 1
    assert suite_engine.runtime.get_instance(iid).state == "completed"

    # 新实例取到 v2
    iid2 = _start_matrix(suite_engine, "nested")
    (child2,) = _children_of(suite_engine, iid2)
    complete_of(suite_engine, child2.id, "u_cash")
    (grand2,) = _children_of(suite_engine, child2.id)
    assert grand2.definition_revision == 2
    complete_of(suite_engine, grand2.id, "u_compliance")
    assert_no_dangling(suite_engine)


def test_terminate_parent_cascades_to_child(suite_engine: ProcessEngine) -> None:
    """第十四轮收口：父 terminate 级联子实例（原「孤儿运行」开放问题已修）。

    来源：本文件曾钉住的开放问题钉子（父 terminate 不级联、子成孤儿）+
    2026-10-09 收口盘点——A9 多实例子流程后孤儿待办面变大，对齐
    Camunda/Zeebe 子树终止（terminate_cascade.py）。
    """
    iid = _start_matrix(suite_engine, "nested")
    (child,) = _children_of(suite_engine, iid)
    suite_engine.runtime.terminate_process(iid, actor="admin", reason="测试终止")
    assert suite_engine.runtime.get_instance(iid).state == "terminated"
    assert suite_engine.runtime.get_instance(child.id).state == "terminated"
