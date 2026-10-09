"""Alembic 环境（kernel-v2 spec §14 尾注）：基线与后续迁移。

数据源＝共享元数据（persistence/orm + orm_v2 + directory/orm 全量注册）；
连接串解析优先级：alembic 配置显式指定（测试/CI 注入）> ``FLOWORK_DB``
环境变量（与 web 同名变量，路径）> 缺省 ``flowork.db``——与
``engine.config.resolve_db_url`` 同约定。
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context

import flowork.directory.orm  # noqa: F401  注册目录表（org_*）
import flowork.persistence.orm_v2  # noqa: F401  注册 v2 表（wf_message_* 等）
from flowork.persistence.db import file_url
from flowork.persistence.orm import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    return file_url(os.environ.get("FLOWORK_DB", "flowork.db"))


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(_database_url(), future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
