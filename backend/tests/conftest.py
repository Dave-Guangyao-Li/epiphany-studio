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


@pytest.fixture(autouse=True)
def isolate_tests_from_local_live_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a developer's untracked live ``.env`` out of deterministic tests.

    Real E2E runs may deliberately use a hosted Provider, a larger Editor
    bundle, and a long Worker cooldown. Those operator settings must not make
    the normal suite call a live model, wait between Fake batches, or change a
    dry-run preflight assertion.
    """

    monkeypatch.setenv("EPIPHANY_MODEL_PROVIDER", "fake")
    monkeypatch.setenv("EPIPHANY_WORKER_BATCH_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("EPIPHANY_DEEPSEEK_MAX_EDITOR_BUNDLE_CHARS", "48000")


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
