"""PG 迁移链与三条装配通道冒烟（Alembic 逐级升级 + CLI/web/env 接线）。

来源：spec §14 尾注（Alembic 基线）此前只在 SQLite 上验证；且 0002/0003
的 ``op.add_column`` 与 create_all 路径在 PG 上的 DDL 差异（server_default
vs Python default）从未对照过。本文件另验证 FLOWORK_DB URL 通道三处接线。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from flowork.api import EngineConfig, FlowBuilder, ProcessEngine
from flowork.persistence.orm import Base

pytestmark = pytest.mark.needs_postgres

ROOT = Path(__file__).parent.parent.parent


def _cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _tables(url: str) -> set[str]:
    engine = create_engine(url, future=True)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _deploy_linear(engine: ProcessEngine, code: str) -> None:
    flow = (
        FlowBuilder(code)
        .start()
        .user_task("t", assignee="'u1'")
        .end()
        .connect("start", "t")
        .connect("t", "end")
        .build()
    )
    engine.repository.deploy(flow)


def test_alembic_upgrade_chain_on_pg(pg_clean: str) -> None:
    """空库逐级升级 0001→head：表全集与共享元数据一致。"""
    command.upgrade(_cfg(pg_clean), "0001_baseline")
    assert set(Base.metadata.tables) <= _tables(pg_clean)
    assert "alembic_version" in _tables(pg_clean)

    command.upgrade(_cfg(pg_clean), "head")

    assert _tables(pg_clean) == set(Base.metadata.tables) | {"alembic_version"}


def test_alembic_stamp_idempotent_on_pg(pg_clean: str) -> None:
    """create_all 建的库 stamp head 后 upgrade 幂等（PG 上与 SQLite 同结论）。"""
    engine = ProcessEngine(EngineConfig(db_url=pg_clean))
    engine.close()
    before = _tables(pg_clean)

    command.stamp(_cfg(pg_clean), "head")
    command.upgrade(_cfg(pg_clean), "head")

    assert _tables(pg_clean) - {"alembic_version"} == before


def test_migration_columns_exist_under_create_all(pg_clean: str) -> None:
    """增量迁移的列在 create_all 路径下同样存在（两条建库路同构，PG 上对照）。"""
    engine = ProcessEngine(EngineConfig(db_url=pg_clean))
    engine.close()
    db = create_engine(pg_clean, future=True)
    try:
        instance_cols = {c["name"] for c in inspect(db).get_columns("wf_process_instance")}
        assert "simulated" in instance_cols  # 0002 增量列
        token_cols = {c["name"] for c in inspect(db).get_columns("wf_token")}
        assert "manual_participants" in token_cols  # 令牌扩展列
    finally:
        db.dispose()


def test_flowork_db_env_url_drives_alembic(pg_clean: str, monkeypatch) -> None:
    """migrations/env.py 的 FLOWORK_DB URL 分支：不设 alembic 显式 url 也连得上 PG。"""
    monkeypatch.setenv("FLOWORK_DB", pg_clean)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")

    assert set(Base.metadata.tables) <= _tables(pg_clean)


def test_cli_db_option_accepts_url(pg_clean: str) -> None:
    """CLI --db 传入 PG URL：建库成功（参数为 str，不折叠 ://）。"""
    from typer.testing import CliRunner

    from flowork.cli.app import app

    result = CliRunner().invoke(app, ["init", "--db", pg_clean])
    assert result.exit_code == 0, result.output
    assert set(Base.metadata.tables) <= _tables(pg_clean)


def test_web_app_accepts_pg_url(pg_clean: str) -> None:
    """create_app(db_file=<PG URL>)：web 装配通道建库成功。"""
    from flowork.contrib.web.app import create_app

    app = create_app(db_file=pg_clean, seed=False)
    try:
        assert set(Base.metadata.tables) <= _tables(pg_clean)
    finally:
        app.state.engine.close()


def test_json_and_boolean_columns_round_trip(pg_clean: str) -> None:
    """JSON 与 Boolean 列在 PG 上往返（布尔为真 bool、JSON 保形状）。"""
    engine = ProcessEngine(EngineConfig(db_url=pg_clean))
    try:
        _deploy_linear(engine, "pg_types")
        instance_id = engine.runtime.start_process(
            "pg_types", start_user="u1", variables={"flag": True, "n": 7, "tags": ["a", "b"]}
        )
        values = engine.runtime.variables_of(instance_id)

        with engine._db_engine.connect() as conn:
            simulated = conn.execute(
                text("SELECT simulated FROM wf_process_instance WHERE id = :i"),
                {"i": instance_id},
            ).scalar_one()
        assert simulated is False  # Boolean 列在 PG 上是真 bool，不是 0/1
        assert values["flag"] is True and values["n"] == 7 and values["tags"] == ["a", "b"]
    finally:
        engine.close()
