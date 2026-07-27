from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from epiphany.db import Database
from epiphany.runtime.orchestrator import Orchestrator
from epiphany.runtime.providers import FakeProvider
from epiphany.runtime.worker import Worker
from epiphany.services import RunService


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest_asyncio.fixture
async def runtime(
    database_url: str,
) -> AsyncIterator[tuple[Database, RunService, Worker]]:
    database = Database(database_url)
    await database.create_schema()
    orchestrator = Orchestrator(task_max_attempts=2)
    service = RunService(database, orchestrator)
    worker = Worker(
        database=database,
        orchestrator=orchestrator,
        provider=FakeProvider(),
        lease_seconds=30,
        timeout_seconds=5,
        poll_interval_seconds=0.01,
    )
    yield database, service, worker
    await database.close()
