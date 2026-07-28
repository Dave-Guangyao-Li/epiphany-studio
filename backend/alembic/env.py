from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from epiphany.config import ensure_sqlite_parent
from epiphany.models import Base

config = context.config
if config.config_file_name is not None:
    # Programmatic migration callers (for example the bounded live-smoke
    # harness) share a process with application loggers. Alembic must configure
    # its own output without disabling those existing loggers.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

if database_url := os.getenv("EPIPHANY_DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", database_url)

ensure_sqlite_parent(config.get_main_option("sqlalchemy.url"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_sync_migrations(connection: object) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(run_sync_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
