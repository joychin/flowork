"""Alembic 基线（kernel-v2 spec §14 尾注）：upgrade head 建全表；create_all 库 stamp 对齐。

两条建库路（引擎启动 create_all / alembic upgrade head）同源同构；
既有 create_all 库先 stamp 再 upgrade 幂等无损。
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import flowork.directory.orm
import flowork.persistence.orm_v2  # noqa: F401  注册 v2 表
from flowork.persistence.db import create_schema
from flowork.persistence.orm import Base

ROOT = Path(__file__).parent.parent


def _cfg(db_path: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _tables(db_path: Path) -> set[str]:
    return set(inspect(create_engine(f"sqlite:///{db_path}")).get_table_names())


def test_upgrade_head_creates_all_tables(tmp_path: Path):
    db = tmp_path / "fresh.db"
    command.upgrade(_cfg(db), "head")
    assert _tables(db) == set(Base.metadata.tables) | {"alembic_version"}


def test_create_all_database_stamps_head_cleanly(tmp_path: Path):
    """既有 create_all 库：stamp 对齐版本标记（无 DDL），再 upgrade 幂等。"""
    db = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db}")
    create_schema(engine)
    engine.dispose()
    before = _tables(db)

    command.stamp(_cfg(db), "head")
    command.upgrade(_cfg(db), "head")

    assert "alembic_version" in _tables(db)  # 仅多出版本标记
    assert _tables(db) - {"alembic_version"} == before
    assert set(Base.metadata.tables) <= _tables(db)
