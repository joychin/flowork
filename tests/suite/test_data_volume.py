"""数据量测试：批量实例世界的 seeding、索引检索、分页完整性、工作台聚合、
混合生命周期状态过滤、驳回循环一致性、规模目录、事件归因。"""

from __future__ import annotations

from flowork.directory.store import DirectoryStore
from flowork.engine.engine import ProcessEngine
from tests.suite.features import covers
from tests.suite.walkthrough import complete_of, reject_of, task_of

HIGH = 5000  # 报销高额阈值


def seed_expense(engine: ProcessEngine, amounts: list[int], user: str = "u_dev_wu") -> list[str]:
    return [
        engine.runtime.start_process(
            "suite_expense",
            start_user=user,
            variables={"amount": amount, "expense_type": "office"},
        )
        for amount in amounts
    ]


def drive_expense(engine: ProcessEngine, iid: str, amount: int) -> None:
    """按金额把实例驱动到完结（走查断言见 walkthrough 文件，这里做批量驱动）。"""
    if amount >= HIGH:
        for assignee in ("u_chief_acc", "u_cash", "u_cfo", "u_ceo"):
            complete_of(engine, iid, assignee)
    else:
        complete_of(engine, iid, "u_lead_be")


def went_high_lane(engine: ProcessEngine, iid: str) -> bool:
    return any(
        row.activity_code == "fin_countersign"
        for row in engine.history.task_history(instance_id=iid)
    )


def test_batch_expense_complete_on_expected_lanes(suite_engine: ProcessEngine) -> None:
    """批量 30 单（含阈值两侧与边界值）：每单落在预期支路并全部完结。"""
    engine = suite_engine
    amounts = [
        800,
        6000,
        4999,
        5000,
        20000,
        1200,
        5001,
        4998,
        9000,
        300,
        7000,
        2500,
        15000,
        600,
        8000,
        100000,
        100,
        5100,
        4800,
        6400,
        2200,
        9500,
        1300,
        5200,
        4700,
        7700,
        1900,
        6100,
        2800,
        8800,
    ]
    pairs = list(zip(seed_expense(engine, amounts), amounts, strict=True))
    for iid, amount in pairs:
        drive_expense(engine, iid, amount)

    for iid, amount in pairs:
        info = engine.runtime.get_instance(iid)
        assert info.state == "completed", iid
        assert went_high_lane(engine, iid) == (amount >= HIGH), amount
    assert len(engine.history.instances(definition_code="suite_expense")) == 30


@covers("contract.variable_index_search")
def test_variable_index_search_precise_sets_at_volume(suite_engine: ProcessEngine) -> None:
    """索引检索在批量数据下精确命中集合，边界值两侧归属正确。"""
    from flowork.persistence.variable_index import instances_by_variable

    engine = suite_engine
    amounts = [800, 4999, 5000, 5001, 20000, 6000, 4998, 1200, 20000, 800]
    iids = seed_expense(engine, amounts)

    with engine.session_factory() as session:
        assert set(instances_by_variable(session, "amount", number=800)) == {iids[0], iids[9]}
        high = {iid for iid, amount in zip(iids, amounts, strict=True) if amount > HIGH}
        low = {iid for iid, amount in zip(iids, amounts, strict=True) if amount < HIGH}
        assert set(instances_by_variable(session, "amount", number_gt=HIGH)) == high
        assert set(instances_by_variable(session, "amount", number_lt=HIGH)) == low
        assert set(instances_by_variable(session, "amount", number=5000)) == {iids[2]}, (
            "边界值恰在阈值上：等值检索独立命中"
        )


@covers("query.paged_tasks")
def test_paged_tasks_full_walk_no_overlap_no_gap(suite_engine: ProcessEngine) -> None:
    """26 高额 + 2 简易 = 106 条已办：limit=7 全量翻页，不重不漏。"""
    engine = suite_engine
    amounts = [6000 + 100 * i for i in range(26)] + [800, 1200]
    iids = seed_expense(engine, amounts)
    for iid, amount in zip(iids, amounts, strict=True):
        drive_expense(engine, iid, amount)

    collected: list[str] = []
    offset = 0
    while True:
        page = engine.history.paged_tasks(offset=offset, limit=7)
        assert page.total == 106
        collected.extend(item.task_id for item in page.items)
        offset += len(page.items)
        if offset >= page.total:
            break
    assert len(collected) == len(set(collected)) == 106, "翻页不重不漏"
    assert set(collected) == {row.task_id for row in engine.history.task_history(limit=500)}


def test_workbench_aggregates_assignee_across_instances(suite_engine: ProcessEngine) -> None:
    """办理人工作台视图：跨实例聚合待办，逐件办理后逐实例推进。"""
    engine = suite_engine
    iids = seed_expense(engine, [9000] * 10)

    tasks = engine.task.list_tasks(assignee="u_chief_acc")
    assert len(tasks) == 10 and {t.instance_id for t in tasks} == set(iids)

    for iid in iids:
        engine.task.complete(task_of(engine, iid, "u_chief_acc"), actor="u_chief_acc")
    assert engine.task.list_tasks(assignee="u_chief_acc") == []
    assert len(engine.task.list_tasks(assignee="u_cash")) == 10, "1/2 未达出口"

    for iid in iids:
        engine.task.complete(task_of(engine, iid, "u_cash"), actor="u_cash")
    assert len(engine.task.list_tasks(assignee="u_cfo")) == 10, "逐级签批第一站齐到"


@covers("query.instance_states", "query.list_tasks")
def test_state_filters_with_mixed_lifecycle(suite_engine: ProcessEngine) -> None:
    """混合生命周期的实例世界：状态过滤与定义过滤精确。"""
    engine = suite_engine
    done_quick = seed_expense(engine, [800, 900])
    for iid in done_quick:
        drive_expense(engine, iid, 800)

    paused = seed_expense(engine, [7000])[0]
    engine.runtime.pause_process(paused, actor="u_cto")

    running = seed_expense(engine, [7100, 7200])

    fragile = engine.runtime.start_process("suite_fragile", start_user="u_dev_wu")
    import pytest

    from flowork.errors import FloworkError, TaskNotAvailableError

    with pytest.raises(FloworkError):
        complete_of(engine, fragile, "u_dev_wu")  # boom → suspended

    assert {row.id for row in engine.history.instances(state="completed")} == set(done_quick)
    assert [row.id for row in engine.history.instances(state="paused")] == [paused]
    assert {row.id for row in engine.history.instances(state="running")} == set(running)
    assert [row.id for row in engine.history.instances(state="suspended")] == [fragile]
    assert [row.id for row in engine.history.instances(definition_code="suite_fragile")] == [
        fragile
    ]
    assert len(engine.task.list_tasks(instance_id=paused)) == 2, "暂停待办仍可见（拦办理不拦查询）"
    with pytest.raises(TaskNotAvailableError):
        engine.task.complete(engine.task.list_tasks(instance_id=paused)[0].id, actor="u_chief_acc")


def test_reject_resubmit_loop_keeps_append_only_history(suite_engine: ProcessEngine) -> None:
    """同一实例 5 轮驳回-重提循环：历史只追加、终局无未完令牌、金额以末次为准。"""
    engine = suite_engine
    iid = seed_expense(engine, [6000])[0]
    complete_of(engine, iid, "u_chief_acc")

    snapshot: list[str] = []
    for _round in range(5):
        reject_of(engine, iid, "u_cash")
        snapshot = [row.id for row in engine.history.task_history(instance_id=iid)]
        complete_of(engine, iid, "u_dev_wu", variables={"amount": 6000})
        complete_of(engine, iid, "u_chief_acc")

    reject_of(engine, iid, "u_cash")  # 末轮改小额走快道收尾
    complete_of(engine, iid, "u_dev_wu", variables={"amount": 800})
    complete_of(engine, iid, "u_lead_be")

    final = [row.id for row in engine.history.task_history(instance_id=iid)]
    assert final[: len(snapshot)] == snapshot, "循环中历史只追加不修改"
    assert engine.runtime.get_instance(iid).state == "completed"
    assert all(t.state != "unfinished" for t in engine.history.tokens(iid))
    archive = engine.history.archive_instance(iid)
    assert archive["instance"]["variables"]["paid_amount"] == 800


def test_directory_at_scale_keeps_scoped_resolution(tmp_path) -> None:
    """规模目录：新增 6 个子部门/36 用户后，范围化解析仍是最近命中层胜出。"""
    from tests.suite.world_org import build_directory_store

    store: DirectoryStore = build_directory_store(tmp_path / "scale.db")
    for squad in range(6):
        code = f"rd_be_squad{squad}"
        store.create_org_unit(code, name=f"战组{squad}", parent_code="rd_be")
        for member in range(6):
            store.create_user(f"u_squad{squad}_{member}", org_unit_code=code)
        store.set_org_unit_manager(code, manager_user_id=f"u_squad{squad}_0")
    store.assign_role("u_squad0_0", "dept_manager", org_unit_code="rd_be_squad0")

    assert store.users_with_role("dept_manager", within="rd_be_squad0") == ["u_squad0_0"]
    assert store.users_with_role("dept_manager", within="rd_be_squad1") == ["u_cto"], (
        "本层无授权向上命中研发中心"
    )
    assert store.manager_of("u_squad3_2") == "u_squad3_0", "主管取自本部门（不向上继承）"
    assert len(store.users_in_department("rd", recursive=True)) == 6 + 36  # 原有 6 人 + 战组 36 人
    assert store.is_member("u_squad5_4", "org:rd_be_squad5")


def test_events_carry_attribution_and_stream_order(suite_engine: ProcessEngine) -> None:
    """批量世界的归因铁律：每条已办有 acted_by；task_completed 事件载荷带 actor；
    每实例事件流按时间非降序。"""
    engine = suite_engine
    iids = seed_expense(engine, [9000, 8500, 7800])
    for iid in iids:
        drive_expense(engine, iid, 9000)

    for iid in iids:
        rows = engine.history.task_history(instance_id=iid)
        assert rows and all(row.acted_by for row in rows), iid
        events = engine.history.events(iid)
        stamps = [event.occurred_at for event in events]
        assert stamps == sorted(stamps)
        completed = [e for e in events if e.event_name == "task_completed"]
        assert len(completed) == 4
        assert all(e.payload.get("actor") for e in completed)
