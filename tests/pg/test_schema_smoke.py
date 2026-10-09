"""PG 方言冒烟：建表全集、时间戳归一、大变量与中文往返。

来源：spec §14「PostgreSQL 生产一等」承诺此前零验证；本文件覆盖
「SQLite 上跑得通但 PG 上可能变形」的三类基础行为（DDL 全集/naive UTC/
JSON 列大值与多字节内容）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from flowork.api import FlowBuilder, ProcessEngine
from flowork.clock import utc_now
from flowork.definitions.node import DeadlineAction, TaskDeadline
from flowork.persistence.orm import Base, ProcessInstanceRow

pytestmark = pytest.mark.needs_postgres


def _instance_row(engine: ProcessEngine, instance_id: str) -> ProcessInstanceRow:
    row = engine.execute(lambda ctx: ctx.session.get(ProcessInstanceRow, instance_id))
    assert row is not None
    return row


def _deploy_waiting_flow(engine: ProcessEngine) -> None:
    """一条停在 user_task 的直线流程（带时限，用于时间戳断言）。"""
    flow = (
        FlowBuilder("pg_pending")
        .start()
        .user_task(
            "review",
            name="审批",
            assignee="'u_reviewer'",
            deadlines=[TaskDeadline(after_minutes=60, action=DeadlineAction.AUTO_REJECT)],
        )
        .end()
        .connect("start", "review")
        .connect("review", "end")
        .build()
    )
    engine.repository.deploy(flow)


def test_create_all_covers_shared_metadata(pg_engine: ProcessEngine) -> None:
    """引擎启动建表：PG 上建出的表集合与共享元数据全等（含 org_* 目录表）。"""
    created = set(inspect(pg_engine._db_engine).get_table_names())
    assert created == set(Base.metadata.tables)


def test_timestamps_are_naive_and_comparable(pg_engine: ProcessEngine) -> None:
    """时间戳往返：PG 上读出仍是 naive（UTC 约定不被 timestamp 列污染）且可比较。

    用真实时钟（默认 config.now）：列级默认与 due_at 同源可比。刻意不用
    FakeClock——注入时钟下 ``started_at``/``created_at`` 列级默认仍走墙钟
    （``orm._now``），与 ``due_at``（config.now）分属两条时间线，属既有
    时钟分叉（非 PG 方言问题），不在本套件断言范围。
    """
    _deploy_waiting_flow(pg_engine)
    instance_id = pg_engine.runtime.start_process("pg_pending", start_user="u0")
    row = _instance_row(pg_engine, instance_id)
    assert row.started_at.tzinfo is None
    assert row.started_at <= utc_now()

    task = pg_engine.task.list_tasks(instance_id=instance_id)[0]
    assert task.due_at is not None
    assert task.due_at.tzinfo is None
    assert task.created_at is not None and task.created_at.tzinfo is None
    assert task.due_at > task.created_at  # 时限 = 创建时刻 + 60 分钟（同源墙钟）


def test_large_and_unicode_variables_round_trip(pg_engine: ProcessEngine) -> None:
    """~200KB 大变量与中文键值完整往返（JSON 列在多字节/大值下不失真）。"""
    _deploy_waiting_flow(pg_engine)
    payload = "审" * 100_000  # 多字节内容 ≈300KB UTF-8
    instance_id = pg_engine.runtime.start_process(
        "pg_pending", start_user="u0", variables={"备注": payload, "金额": 12345}
    )
    row = _instance_row(pg_engine, instance_id)
    assert row.variables["备注"] == payload
    assert row.variables["金额"] == 12345

    values = pg_engine.runtime.variables_of(instance_id)
    assert values["备注"] == payload
