"""全库不变量断言（悬空检查）：走查收尾用，防「以为走完了实际留僵尸」。

显式调用而非 autouse——挂起/暂停/等消息类测试有意留下运行态实例，
强制收尾断言会误伤；由认为「应当干净」的测试自己在结尾调用。
全部走公开查询面（history.instances/tokens、task.list_tasks），不触 ORM。
"""

from __future__ import annotations

from collections.abc import Collection

from flowork.engine.engine import ProcessEngine

# 实例终态（与 runtime/lifecycle.py 的 InstanceState 对齐；failed 亦是终态）
TERMINAL_INSTANCE_STATES = frozenset({"completed", "terminated", "cancelled", "failed"})
# 令牌终态（TokenState：unfinished 之外皆终态；revoked=撤回、failed=节点故障）
TERMINAL_TOKEN_STATES = frozenset({"finished", "cancelled", "revoked", "failed"})


def assert_no_dangling(engine: ProcessEngine, *, exclude: Collection[str] = ()) -> None:
    """全库悬空检查：无非终态实例、无活跃令牌、无 ready 待办。

    exclude：有意留在运行态的实例 id（如混跑场景里故意不办完的在办实例）。
    抓的问题：测试以为走完了，实际留下僵尸实例/待办，
    后续测试查列表时多出一条，失败点离病因十万八千里。
    """
    skipped = set(exclude)

    instances = engine.history.instances()
    dangling = [
        (row.id, row.state)
        for row in instances
        if row.id not in skipped and row.state not in TERMINAL_INSTANCE_STATES
    ]
    assert not dangling, f"存在非终态实例：{dangling}"

    for row in instances:
        if row.id in skipped:
            continue
        active_tokens = [
            (t.activity_code, t.state)
            for t in engine.history.tokens(row.id)
            if t.state not in TERMINAL_TOKEN_STATES
        ]
        assert not active_tokens, f"实例 {row.id} 存在活跃令牌：{active_tokens}"

    leftover_tasks = [
        (t.instance_id, t.activity_code, t.assignee)
        for t in engine.task.list_tasks()
        if t.instance_id not in skipped
    ]
    assert not leftover_tasks, f"存在遗留待办：{leftover_tasks}"
