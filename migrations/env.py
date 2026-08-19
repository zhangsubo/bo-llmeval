"""Alembic 运行环境：从 Settings 读取 DSN，alembic.ini 不含任何连接串。

T-01-02 缓解：DSN 只来自环境变量（经 evalplat.config.Settings），
防止密码随 alembic.ini 入库。

URL 解析优先级：
1. ``config.get_main_option("sqlalchemy.url")`` —— 测试代码通过
   ``Config.set_main_option`` 显式注入（如指向 db-test 的 TEST_DATABASE_URL）
2. ``ALEMBIC_DATABASE_URL`` 环境变量 —— 运维/CI 覆盖
3. ``Settings.database_url`` —— 常规开发/compose migrate 服务
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from evalplat.config import get_settings
from evalplat.db.base import Base

# 导入全部模型以确保 metadata 完整（autogenerate 依赖）
import evalplat.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    override = os.environ.get("ALEMBIC_DATABASE_URL")
    if override:
        return override
    return get_settings().database_url


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL 脚本，不连接数据库。"""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移（PostgreSQL 支持 DDL 事务，默认整段原子提交）。"""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
