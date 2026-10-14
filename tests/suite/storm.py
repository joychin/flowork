"""随机动作风暴执行器（测试套件完善批 2/5）：行为随机的另一半。

与 random_gen（结构随机）互补——这里结构固定（knot 等复杂世界），随机的是
**人对流程的折腾**：complete/reject/transfer/assist/consult/加签/claim/unclaim/
withdraw/暂停/恢复/终止/取消/时钟推进，任意组合。
裁决规则：动作要么成功、要么收 FloworkError 家族领域拒绝；其他异常=失败。
oracle=引擎自己的动作表（TaskInfo.available_actions，服务端算完下发）：
合法动作只从可用集挑；另有低概率非法探针（不在可用集的动作必须被
TaskNotAvailableError 拒绝——引擎若照做即违规）。
每步后跑账本不变量（批次一，中途安全）；随机相结束后 drain 确定性清场
（resume→resolve(skip)→consult→assist→claim→complete→时钟兜底），
断言「任意风暴都能被办完」。种子驱动，失败三段日志可复现。
"""

from __future__ import annotations

import random
from typing import Any

from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError, TaskNotAvailableError
from tests.suite.invariants import (
    TERMINAL_INSTANCE_STATES,
    assert_child_tree_closed,
    assert_instance_ledger,
)

_USERS = [
    "u_dev_wu",
    "u_dev_zheng",
    "u_dev_liu",
    "u_lead_be",
    "u_cash",
    "u_chief_acc",
    "u_cfo",
    "u_buyer",
]
_COMMENTS = ["风暴步", "补一句意见", "加急处理", ""]
_PROBE_POOL = [
    "complete",
    "reject",
    "transfer",
    "assist",
    "consult",
    "add_countersigner",
    "unclaim",
]


class StormFailure(AssertionError):
    """风暴失败：携带种子+世界+动作序列的三段报告。"""


def run_storm(
    engine: ProcessEngine,
    *,
    seed: int,
    world_code: str,
    start_user: str,
    variables: dict[str, Any],
    max_steps: int = 40,
) -> tuple[str, list[str]]:
    """随机相：发起实例并随机打 max_steps 步，返回 (实例 id, 动作日志)。"""
    rng = random.Random(seed)
    iid = engine.runtime.start_process(world_code, start_user=start_user, variables=variables)
    log: list[str] = []
    try:
        for _ in range(max_steps):
            if engine.runtime.get_instance(iid).state in TERMINAL_INSTANCE_STATES:
                break
            _one_step(engine, rng, iid, log)
            assert_instance_ledger(engine, iid)  # 每步账本（中途安全）
            assert_child_tree_closed(engine)
    except Exception as exc:
        raise StormFailure(_report(seed, world_code, log, exc)) from exc
    return iid, log


def drain(engine: ProcessEngine, iid: str, *, max_rounds: int = 400) -> list[str]:
    """收敛相：确定性清场——任意风暴残留都能被办完（有界迭代推到终态）。"""
    log: list[str] = []
    for _ in range(max_rounds):
        state = engine.runtime.get_instance(iid).state
        if state in TERMINAL_INSTANCE_STATES:
            return log
        if state == "paused":
            engine.runtime.resume_process(iid, actor="storm:drain")
            log.append("resume")
            continue
        tree = _tree_of(engine, iid)
        open_incidents = [
            inc
            for inc in engine.runtime.list_incidents()
            if inc["instance_id"] in tree and inc["state"] == "open"
        ]
        if open_incidents:
            engine.runtime.resolve_incident(
                open_incidents[0]["id"], action="skip", actor="storm:drain"
            )
            log.append(f"resolve(skip) {open_incidents[0]['id'][:8]}")
            continue
        tasks = _ready_tasks(engine, tree)
        if not tasks:  # 无待办必有未到期 timer（wait/时限）：推钟兜底
            _advance(engine, 15)
            log.append("advance 15 + tick")
            continue
        pick = _pick_drain_task(tasks)
        try:
            if pick.task_type == "consult":
                engine.task.complete_consult(pick.id, actor=pick.assignee)
            elif pick.task_type == "assist":
                engine.task.complete_assist(pick.id, actor=pick.assignee)
            elif pick.assignee == "":
                engine.task.claim(pick.id, _USERS[3])
            else:
                engine.task.complete(pick.id, actor=pick.assignee)
            log.append(f"drain {pick.activity_code}#{pick.id[:8]}")
        except FloworkError as exc:  # 清场撞领域拒绝=状态变了，下一轮重判
            log.append(f"drain-rejected {pick.activity_code}：{exc}")
    raise AssertionError(
        f"清场超时：实例 {iid} 仍 {engine.runtime.get_instance(iid).state}，日志尾={log[-8:]}"
    )


def _pick_drain_task(tasks):
    consults = [t for t in tasks if t.task_type == "consult"]
    if consults:
        return consults[0]
    assists = [t for t in tasks if t.task_type == "assist"]
    if assists:
        return assists[0]
    return tasks[0]


# —— 单步动作选择（加权类别，类别空则回落时钟推进）——


def _one_step(engine: ProcessEngine, rng: random.Random, iid: str, log: list[str]) -> None:
    roll = rng.random()
    if roll < 0.60:
        _act_on_task(engine, rng, iid, log)
    elif roll < 0.70:
        _try_withdraw(engine, rng, iid, log)
    elif roll < 0.82:
        _instance_action(engine, rng, iid, log)
    elif roll < 0.95:
        minutes = rng.randint(1, 30)
        log.append(f"advance {minutes} + tick")
        _advance(engine, minutes)
    else:
        _illegal_probe(engine, rng, log)


def _act_on_task(engine: ProcessEngine, rng: random.Random, iid: str, log: list[str]) -> None:
    tree = _tree_of(engine, iid)
    tasks = _ready_tasks(engine, tree)
    if not tasks:
        minutes = rng.randint(1, 15)
        log.append(f"advance {minutes} + tick（无待办）")
        _advance(engine, minutes)
        return
    task = rng.choice(tasks)
    if not task.available_actions:
        log.append(f"noop {task.activity_code}（ready 但动作表为空）")
        return
    _dispatch(engine, rng, task, rng.choice(task.available_actions), log)


def _dispatch(
    engine: ProcessEngine,
    rng: random.Random,
    task,
    action: str,
    log: list[str],
    *,
    probe: bool = False,
) -> None:
    others = [u for u in _USERS if u != task.assignee]
    tag = f"{'probe' if probe else 'do'} {action} {task.activity_code}#{task.id[:8]}"
    log.append(tag)
    try:
        if action == "complete":
            engine.task.complete(task.id, actor=task.assignee, comment=rng.choice(_COMMENTS))
        elif action == "reject":
            engine.task.reject(task.id, actor=task.assignee, comment="风暴否决")
        elif action == "transfer":
            engine.task.transfer(task.id, to_user=rng.choice(others), actor=task.assignee)
        elif action == "assist":
            engine.task.assist(task.id, [rng.choice(others)], actor=task.assignee)
        elif action == "add_countersigner":
            engine.task.add_countersigner(task.id, [rng.choice(others)], actor=task.assignee)
        elif action == "consult":
            engine.task.consult(task.id, [rng.choice(others)], actor=task.assignee)
        elif action == "unclaim":
            engine.task.unclaim(task.id, user=task.assignee)
        elif action == "claim":
            engine.task.claim(task.id, rng.choice(_USERS))
        elif action == "complete_assist":
            engine.task.complete_assist(task.id, actor=task.assignee)
        elif action == "complete_consult":
            engine.task.complete_consult(
                task.id, actor=task.assignee, opinion=rng.choice(["agree", "reject"])
            )
        else:
            raise AssertionError(f"风暴执行器未知动作：{action}")
        if probe:
            # 动作表外的动作被引擎照做=动作表失真，按违规上报
            raise AssertionError(f"非法探针被接受：{action} 不在 {task.activity_code} 可用集")
    except TaskNotAvailableError as exc:
        if not probe:
            log.append(f"  ↳ 领域拒绝：{exc}")
    except FloworkError as exc:
        if probe:
            log.append(f"  ↳ 探针领域拒绝（非动作表路径）：{exc}")
        else:
            log.append(f"  ↳ 领域拒绝：{exc}")


def _try_withdraw(engine: ProcessEngine, rng: random.Random, iid: str, log: list[str]) -> None:
    votes = [
        row
        for row in engine.history.task_history(iid, limit=100_000)
        if row.task_type == "approval" and row.outcome in ("agree", "reject")
    ]
    if not votes:
        log.append("withdraw 无票可撤（回落时钟）")
        _advance(engine, rng.randint(1, 15))
        return
    row = rng.choice(votes)
    log.append(f"do withdraw {row.activity_code}#{row.task_id[:8]} by {row.assignee}")
    try:
        engine.task.withdraw(row.task_id, actor=row.assignee)
    except FloworkError as exc:
        log.append(f"  ↳ 领域拒绝：{exc}")


def _instance_action(engine: ProcessEngine, rng: random.Random, iid: str, log: list[str]) -> None:
    state = engine.runtime.get_instance(iid).state
    options = (
        ["pause", "pause", "resume"]
        if state == "running"
        else ["resume"]
        if state == "paused"
        else []
    )
    if not options or rng.random() < 0.15:  # 小概率直接终局
        options += ["terminate", "cancel"]
    action = rng.choice(options)
    log.append(f"do {action}_process")
    try:
        if action == "pause":
            engine.runtime.pause_process(iid, actor="storm:walker")
        elif action == "resume":
            engine.runtime.resume_process(iid, actor="storm:walker")
        elif action == "terminate":
            engine.runtime.terminate_process(iid, actor="storm:walker")
        else:
            engine.runtime.cancel_process(iid, actor="storm:walker")
    except FloworkError as exc:
        log.append(f"  ↳ 领域拒绝：{exc}")


def _illegal_probe(engine: ProcessEngine, rng: random.Random, log: list[str]) -> None:
    tasks = [t for t in engine.task.list_tasks(state=None) if t.state == "ready"]
    probes = [t for t in tasks if set(_PROBE_POOL) - set(t.available_actions)]
    if probes:
        task = rng.choice(probes)
        candidates = [a for a in _PROBE_POOL if a not in task.available_actions]
        _dispatch(engine, rng, task, rng.choice(candidates), log, probe=True)
        return
    history = engine.history.task_history(limit=50)
    if history:
        row = rng.choice(history)
        log.append(f"probe complete 已办 {row.activity_code}#{row.task_id[:8]}")
        try:
            engine.task.complete(row.task_id, actor=row.assignee)
        except FloworkError as exc:
            log.append(f"  ↳ 探针领域拒绝：{exc}")
        else:
            raise AssertionError(f"已办任务 {row.task_id} 仍可 complete——状态守卫失守")
    else:
        log.append("probe 无目标（空库）")


def _advance(engine: ProcessEngine, minutes: int) -> None:
    advance = getattr(engine.config.now, "advance", None)
    if advance is not None:
        advance(minutes)
    engine.jobs.tick()


def _tree_of(engine: ProcessEngine, iid: str) -> set[str]:
    """实例树（本尊+全部后代）：动作池与故障单判定范围。"""
    tree = {iid}
    frontier = [iid]
    while frontier:
        parent = frontier.pop()
        for row in engine.history.instances(limit=100_000):
            if row.parent_id == parent and row.id not in tree:
                tree.add(row.id)
                frontier.append(row.id)
    return tree


def _ready_tasks(engine: ProcessEngine, tree: set[str]):
    return [t for tid in tree for t in engine.task.list_tasks(instance_id=tid, state="ready")]


def _report(seed: int, world_code: str, log: list[str], exc: Exception) -> str:
    lines = [f"动作风暴失败 seed={seed} world={world_code}：{exc!r}", "", "── 动作序列 ──"]
    lines.extend(f"  {index:3d} {descr}" for index, descr in enumerate(log))
    return "\n".join(lines)
