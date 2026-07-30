from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from epiphany.config import ensure_sqlite_parent
from epiphany.models import Base

logger = logging.getLogger("epiphany.database")
SQLITE_ASYNC_PREFIX = "sqlite+aiosqlite:///"


def _sqlite_read_only_url(database_url: str) -> str:
    if not database_url.startswith(SQLITE_ASYNC_PREFIX):
        raise ValueError("read-only Database currently supports SQLite only")
    raw_path = database_url.removeprefix(SQLITE_ASYNC_PREFIX)
    if raw_path in {"", ":memory:"} or raw_path.startswith("file:"):
        raise ValueError("read-only Database requires a filesystem SQLite path")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    encoded_path = quote(str(path), safe="/")
    return f"{SQLITE_ASYNC_PREFIX}file:{encoded_path}?mode=ro&uri=true"


class Database:
    def __init__(self, database_url: str, *, read_only: bool = False) -> None:
        if read_only:
            database_url = _sqlite_read_only_url(database_url)
        else:
            ensure_sqlite_parent(database_url)
        self.engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        if database_url.startswith("sqlite"):
            self._configure_sqlite(read_only=read_only)

    def _configure_sqlite(self, *, read_only: bool) -> None:
        @event.listens_for(self.engine.sync_engine, "connect")
        def set_sqlite_pragmas(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            if read_only:
                cursor.execute("PRAGMA query_only=ON")
            else:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        logger.info(
            "Database schema ready",
            extra={"event": "database.schema.ready"},
        )

    async def drop_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()
