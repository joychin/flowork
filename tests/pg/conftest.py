"""PostgreSQL 冒烟套件基建：容器自动起删 + 引擎夹具（marker: needs_postgres）。

来源：spec §14 支持矩阵「PostgreSQL 生产一等」此前零验证——全部测试跑在
SQLite 上，方言行为（行锁并发/JSON 列/时间戳归一/迁移链）从未在真 PG 上
对照过。本套件补齐该证据面。

连接来源两级：``FLOWORK_TEST_PG_URL`` 优先（现成实例/CI service）；
未设则用 docker 起一次性容器（``--rm`` 自动删，随机宿主端口），
docker 不可用时整组 skip（不影响默认全量套件）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from flowork.api import EngineConfig, ProcessEngine
from flowork.clock import utc_now

PG_IMAGE = "postgres:16-alpine"
PG_PASSWORD = "flowork"
PG_DB = "flowork_test"


class FakeClock:
    """可推进的确定性时钟（与 tests/edge/conftest.py 同形态）。"""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 9, 0, 0)

    def __call__(self) -> datetime:
        return self._now

    def advance_minutes(self, minutes: int) -> None:
        self._now += timedelta(minutes=minutes)


def _local_docker_url(container: str) -> str:
    """读回容器映射到宿主机的随机端口。"""
    result = subprocess.run(
        ["docker", "port", container, "5432/tcp"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    # 形如 "0.0.0.0:54321"（可能多行，取第一条）
    port = result.stdout.strip().splitlines()[0].rsplit(":", 1)[-1]
    return f"postgresql+psycopg://postgres:{PG_PASSWORD}@127.0.0.1:{port}/{PG_DB}"


def _wait_ready(url: str, *, timeout_seconds: float = 45.0) -> None:
    """等待 PG 就绪：连接 + SELECT 1 成功为止。"""
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            engine = create_engine(url, future=True)
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            finally:
                engine.dispose()
        except Exception as exc:  # 连接期任何异常都算未就绪
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(f"PostgreSQL 未在 {timeout_seconds}s 内就绪：{last_error}")


def _start_container() -> tuple[str, str]:
    """起一次性容器，返回 (container_name, url)。"""
    name = f"flowork-pg-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            f"POSTGRES_PASSWORD={PG_PASSWORD}",
            "-e",
            f"POSTGRES_DB={PG_DB}",
            "-p",
            "127.0.0.1::5432",
            PG_IMAGE,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,  # 首次可能拉镜像
    )
    return name, _local_docker_url(name)


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """PG 连接串。优先 FLOWORK_TEST_PG_URL；否则 docker 起删一次性容器。"""
    provided = os.environ.get("FLOWORK_TEST_PG_URL")
    if provided:
        _wait_ready(provided)
        yield provided
        return

    if shutil.which("docker") is None:
        pytest.skip("无 docker 且未设 FLOWORK_TEST_PG_URL——跳过 PostgreSQL 冒烟套件")
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode != 0:
        pytest.skip("docker daemon 不可用且未设 FLOWORK_TEST_PG_URL——跳过 PostgreSQL 冒烟套件")

    container, url = _start_container()
    try:
        _wait_ready(url)
        yield url
    finally:
        # --rm：stop 即删；daemon 异常时不掩盖测试结果
        subprocess.run(["docker", "stop", container], capture_output=True, text=True, timeout=60)


def _reset_schema(url: str) -> None:
    """清空库（含 alembic_version），等价 SQLite 内存库的「每测试全新库」语义。"""
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


@pytest.fixture()
def pg_clean(pg_url: str) -> Iterator[str]:
    """每个测试前重置 schema 的连接串。"""
    _reset_schema(pg_url)
    yield pg_url


@pytest.fixture()
def pg_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def pg_engines(pg_clean: str) -> Iterator[Callable[..., ProcessEngine]]:
    """PG 引擎工厂：同一 URL 多引擎=多副本（形态对齐 tests/edge 的文件库工厂）。

    时钟缺省为真实墙钟（生产形态，与 ``edge_engines`` 同约定）；需要确定性
    时间的测试显式传 ``now=pg_clock``。
    """
    created: list[ProcessEngine] = []

    def _make(*, now: Callable[[], datetime] | None = None) -> ProcessEngine:
        engine = ProcessEngine(EngineConfig(db_url=pg_clean, now=now or utc_now))
        created.append(engine)
        return engine

    yield _make
    for engine in created:
        engine.close()


@pytest.fixture()
def pg_engine(pg_engines: Callable[..., ProcessEngine]) -> ProcessEngine:
    """单引擎夹具（多数冒烟用）。"""
    return pg_engines()


__all__ = ["FakeClock", "pg_clean", "pg_clock", "pg_engine", "pg_engines", "pg_url"]
