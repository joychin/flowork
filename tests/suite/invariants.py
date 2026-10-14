"""全库不变量断言（悬空+账本）：走查与随机套件收尾用，防「以为走完了实际留僵尸」。

显式调用而非 autouse——挂起/暂停/等消息类测试有意留下运行态实例，
强制收尾断言会误伤；由认为「应当干净」的测试自己在结尾调用。
全部走公开查询面（history.instances/tokens/task_history/events、task.list_tasks），
不触 ORM。账本三断言（票数/对账/终态契约）**中途调用也成立**——计数、归档行、
事件都在同一命令事务里落，随机动作风暴逐步复用（风暴套件 tests/suite/storm.py）。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection

from flowork.engine.engine import ProcessEngine

# 实例终态（与 runtime/lifecycle.py 的 InstanceState 对齐；failed 亦是终态）
TERMINAL_INSTANCE_STATES = frozenset({"completed", "terminated", "cancelled", "failed"})
# 令牌终态（TokenState：unfinished 之外皆终态；revoked=撤回、failed=节点故障）
TERMINAL_TOKEN_STATES = frozenset({"finished", "cancelled", "revoked", "failed"})

# 已办行 outcome → 对应终态事件（对账映射；全集=archive_task 全部调用点+取消路径）。
# consult 行的 outcome 复用 agree/reject，但事件面专用 task_consult_finished——走专用分支。
_ROW_EVENT_MAP = {
    "agree": "task_completed",
    "reject": "task_rejected",
    "transfer": "task_transferred",
    "auto_pass": "task_auto_passed",
    "cancelled": "task_cancelled",
    "assist": "task_assist_finished",
    "assist_cancel": "task_assist_cancelled",
}

# 实例终态 → 终态事件族（existence 对账；completed 兼管理员强制完成）
_TERMINAL_EVENT_FAMILIES = {
    "completed": ("process_completed", "process_finished_by_admin"),
    "terminated": ("process_terminated",),
    "cancelled": ("process_cancelled",),
}


def assert_no_dangling(engine: ProcessEngine, *, exclude: Collection[str] = ()) -> None:
    """全库悬空检查：无非终态实例、无活跃令牌、无 ready 待办。

    exclude：有意留在运行态的实例 id（如混跑场景里故意不办完的在办实例）。
    抓的问题：测试以为走完了，实际留下僵尸实例/待办，
    后续测试查列表时多出一条，失败点离病因十万八千里。
    """
    skipped = set(exclude)

    instances = engine.history.instances(limit=100_000)
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
        for t in engine.task.list_tasks(state=None)
        if t.instance_id not in skipped
    ]
    assert not leftover_tasks, f"存在遗留待办：{leftover_tasks}"


def assert_vote_ledger(engine: ProcessEngine, instance_id: str) -> None:
    """票数账平：每令牌 approve+disapprove == 表决行数 − 撤票回退数。

    依据：complete/reject/withdraw 是仅有的计数改写点（auto_pass 归档不动计数、
    取消路径不动计数）；超时代投走 complete 故也是 agree 行，天然对齐。
    表决行口径与 tasks/vote.py 一致（approval 且 outcome∈{agree,reject}——
    咨询行 outcome 复用 agree/reject 须按 task_type 剔除）。
    """
    retracted: Counter[str] = Counter()
    for event in _events_of(engine, instance_id):
        if event.event_name == "task_withdrawn" and event.payload.get("vote_retracted"):
            retracted[str(event.payload.get("token_id"))] += 1
    votes: Counter[str] = Counter()
    for row in engine.history.task_history(instance_id, limit=100_000):
        if row.task_type == "approval" and row.outcome in ("agree", "reject"):
            votes[row.token_id] += 1
    for token in engine.history.tokens(instance_id):
        expected = votes.get(token.id, 0) - retracted.get(token.id, 0)
        actual = token.approve_count + token.disapprove_count
        assert actual == expected, (
            f"票数账不平：实例 {instance_id} 令牌 #{token.token_no}"
            f"({token.activity_code}) 计数={actual}，表决行−撤票={expected}"
        )


def assert_history_event_reconciliation(engine: ProcessEngine, instance_id: str) -> None:
    """已办行↔事件对账：每条已办行必有同 task_id 的对应终态事件。

    consult 行例外：task_consult_finished 只在链上最后一条意见交回时发射
    （每链一次），非末条 consult 行无专属事件——只核对「若是末条则 opinion
    与行 outcome 一致」。cc/service 节点不落已办行，不在对账范围。
    """
    emitted: set[tuple[str, str]] = set()
    consult_last: dict[str, str] = {}  # last_task_id -> opinion
    for event in _events_of(engine, instance_id):
        task_id = event.payload.get("task_id")
        if task_id:
            emitted.add((event.event_name, str(task_id)))
        if event.event_name == "task_consult_finished":
            consult_last[str(event.payload.get("last_task_id"))] = str(event.payload.get("opinion"))
    for row in engine.history.task_history(instance_id, limit=100_000):
        if row.task_type == "consult":
            if row.task_id in consult_last:
                assert consult_last[row.task_id] == row.outcome, (
                    f"咨询末条意见与事件不一致：{row.task_id} 行={row.outcome}"
                )
            continue  # 非末条咨询行无专属事件（每链一次的设计）
        expected = _ROW_EVENT_MAP.get(row.outcome)
        assert expected is not None, f"未知 outcome={row.outcome!r}：{row.task_id}"
        assert (expected, row.task_id) in emitted, (
            f"已办行无对应事件：实例 {instance_id} 任务 {row.task_id}"
            f" outcome={row.outcome} 应有 {expected}"
        )


def assert_child_tree_closed(engine: ProcessEngine) -> None:
    """子实例树闭合：parent 指向存在实例、无环、父终态⟹子全终态（级联契约）。"""
    rows = _all_instances(engine)
    by_id = {row.id: row for row in rows}
    for row in rows:
        if row.parent_id is not None:
            assert row.parent_id in by_id, (
                f"子实例 {row.id}({row.definition_code}) 的父 {row.parent_id} 不在实例库"
            )
    for row in rows:
        walked: set[str] = set()
        cursor = row
        while cursor.parent_id is not None:
            assert cursor.id not in walked, f"实例树成环（从 {row.id} 上溯撞环）"
            walked.add(cursor.id)
            parent = by_id[cursor.parent_id]
            if parent.state in TERMINAL_INSTANCE_STATES:
                assert cursor.state in TERMINAL_INSTANCE_STATES, (
                    f"父 {parent.id} 已终态({parent.state}) 但子 {cursor.id}"
                    f" 仍 {cursor.state}——级联契约被破坏"
                )
            cursor = parent


def assert_terminal_event_contract(engine: ProcessEngine, instance_id: str) -> None:
    """终态契约：实例终态 ⟹ 存在对应族的终态事件（completed 兼管理员强制完成）。"""
    state = engine.runtime.get_instance(instance_id).state
    if state not in TERMINAL_INSTANCE_STATES:
        return  # 运行中实例不要求（中途调用安全）
    family = _TERMINAL_EVENT_FAMILIES.get(state)
    if family is None:
        return  # failed 终态零写入方，账面上不出现
    names = {event.event_name for event in _events_of(engine, instance_id)}
    assert names & set(family), (
        f"实例 {instance_id} 终态 {state} 但事件流缺终态事件（应含 {family} 之一）"
    )


def assert_instance_ledger(engine: ProcessEngine, instance_id: str) -> None:
    """单实例账本三件：票数账平 + 已办对账 + 终态契约（中途调用也成立）。"""
    assert_vote_ledger(engine, instance_id)
    assert_history_event_reconciliation(engine, instance_id)
    assert_terminal_event_contract(engine, instance_id)


def assert_full_ledger(engine: ProcessEngine, *, exclude: Collection[str] = ()) -> None:
    """全库账本全家：悬空 + 树闭合 + 逐实例三件（exclude 同 assert_no_dangling）。"""
    assert_no_dangling(engine, exclude=exclude)
    assert_child_tree_closed(engine)
    for row in _all_instances(engine):
        if row.id not in set(exclude):
            assert_instance_ledger(engine, row.id)


def _events_of(engine: ProcessEngine, instance_id: str):
    """实例事件全集（翻页拉全；实例维 (occurred_at,id) 全序）。"""
    collected, offset = [], 0
    while True:
        page = engine.history.events(instance_id, limit=500, offset=offset)
        collected.extend(page)
        if len(page) < 500:
            return collected
        offset += 500


def _all_instances(engine: ProcessEngine):
    """实例全集（翻页拉全；默认隔离模拟实例）。"""
    collected, offset = [], 0
    while True:
        page = engine.history.instances(limit=500, offset=offset)
        collected.extend(page)
        if len(page) < 500:
            return collected
        offset += 500
