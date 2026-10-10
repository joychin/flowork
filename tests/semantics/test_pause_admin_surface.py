"""暂停的拦截范围（spec §7，v2.3 第二十轮·初学者问答审计 D2）。

拍板口径：暂停冻结**任务动作面、外部变量写、完成跃迁**；管理员手术
（activate_node / cancel_node / move_token）**明文允许**（运维需要处置冻结
实例）；cancel/terminate 不拦（级联砍树对暂停子照常结算）。
矩阵五例把每个入口 × paused 钉住——重构丢守卫即红。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.engine.engine import ProcessEngine
from flowork.errors import InstanceNotFoundError, TaskNotAvailableError


@pytest.fixture
def engine() -> ProcessEngine:
    return ProcessEngine()


def _paused_instance(engine: ProcessEngine) -> str:
    """a(单人) → b(单人) 的实例，办完 a 后暂停（b 在途）。"""
    definition = (
        FlowBuilder("pause_surface")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    engine.repository.deploy(definition)
    instance_id = engine.runtime.start_process(
        "pause_surface", start_user="u_s", variables={"patched": 0}
    )
    task_a = engine.task.list_tasks(instance_id=instance_id)[0]
    engine.task.complete(task_a.id, actor="u_a")
    engine.runtime.pause_process(instance_id, actor="admin")
    return instance_id


def test_activate_node_allowed_on_paused(engine: ProcessEngine):
    """手术明文允许：暂停实例上激活节点成功（不要求先 resume），暂停态不变。"""
    instance_id = _paused_instance(engine)
    engine.task.activate_node(instance_id, "a", actor="admin")
    assert engine.runtime.get_instance(instance_id).state == "paused"  # 手术不改变暂停态
    assert {t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)} == {
        "a",
        "b",
    }


def test_cancel_node_allowed_on_paused(engine: ProcessEngine):
    """手术明文允许：暂停实例上取消节点成功。"""
    instance_id = _paused_instance(engine)
    engine.task.cancel_node(instance_id, "b", actor="admin")
    assert engine.runtime.get_instance(instance_id).state == "paused"


def test_move_token_allowed_and_stays_paused(engine: ProcessEngine):
    """手术明文允许：暂停实例上 move_token 到中间节点成功，且实例仍 paused
    不自动恢复（B8 第二坑钉子：与故障单 skip 路径的 suspended→running 恢复不同）。
    移到 end 例外——那是完成跃迁，与 finish_instance 同拒（终态只从运行态走）。"""
    instance_id = _paused_instance(engine)
    token_b = next(t for t in engine.history.tokens(instance_id) if t.activity_code == "b")
    engine.runtime.move_token(token_b.id, "a", actor="admin")  # 移回中间节点：允许
    assert engine.runtime.get_instance(instance_id).state == "paused"
    assert {t.activity_code for t in engine.task.list_tasks(instance_id=instance_id)} == {"a"}

    token_a = next(
        t
        for t in engine.history.tokens(instance_id)
        if t.activity_code == "a" and t.state == "unfinished"
    )
    with pytest.raises(TaskNotAvailableError, match="不可直接完成"):
        engine.runtime.move_token(token_a.id, "end", actor="admin")  # 移到 end＝完成，拒


def test_finish_instance_rejected_on_paused(engine: ProcessEngine):
    """完成跃迁拒 paused：终态只从运行态走——清档必须 resume 或改用 cancel。"""
    instance_id = _paused_instance(engine)
    with pytest.raises(TaskNotAvailableError, match="不可直接完成"):
        engine.task.finish_instance(instance_id, actor="admin")
    assert engine.runtime.get_instance(instance_id).state == "paused"
    # 正路一：cancel 允许（级联/清理语义）
    engine.runtime.cancel_process(instance_id, actor="admin")
    assert engine.runtime.get_instance(instance_id).state == "cancelled"


def test_update_variables_rejected_on_paused(engine: ProcessEngine):
    """冻结快照不可写：取证期间变量只读；修数据路径＝resume → 改 → 再 pause。"""
    instance_id = _paused_instance(engine)
    with pytest.raises(InstanceNotFoundError, match="冻结快照"):
        engine.runtime.update_variables(
            instance_id, {"patched": 1}, actor="admin", reason="尝试冻结期写入"
        )
    engine.runtime.resume_process(instance_id, actor="admin")
    engine.runtime.update_variables(instance_id, {"patched": 1}, actor="admin")
    assert engine.runtime.variables_of(instance_id)["patched"] == 1
