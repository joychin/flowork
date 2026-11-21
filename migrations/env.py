"""Alembic 环境（kernel-v2 spec §14 尾注）：基线与后续迁移。

数据源＝共享元数据（persistence/orm + orm_v2 + directory/orm 全量注册）；
连接串解析优先级：alembic 配置显式指定（测试/CI 注入）> ``FLOWORK_DB``
环境变量 > 缺省 ``flowork.db``——与 ``engine.config.resolve_db_url`` 同约定
（``FLOWORK_DB`` 含 ``://`` 即完整数据库 URL，否则按 SQLite 文件路径）。
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context

import flowork.directory.orm
import flowork.persistence.orm_v2  # noqa: F401  注册 v2 表（wf_message_* 等）
from flowork.persistence.db import url_from_value
from flowork.persistence.orm import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False：fileConfig 缺省为 True，会把迁移前已存在
    # 的 logger（嵌入方进程内先 import flowork 再跑升级时＝整个 flowork 命名
    # 空间）静默禁用——引擎日志在宿主进程里无声消失。alembic.ini 只配 alembic
    # 自己的 logger，不碍着既有 logger。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    return url_from_value(os.environ.get("FLOWORK_DB", "flowork.db"))


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
