from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from epiphany.api import router
from epiphany.config import Settings
from epiphany.db import Database
from epiphany.runtime.orchestrator import Orchestrator
from epiphany.runtime.providers import FakeProvider, ModelProvider
from epiphany.runtime.worker import Worker
from epiphany.services import RunService


def create_app(
    *,
    settings: Settings | None = None,
    provider: ModelProvider | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    database = Database(resolved_settings.database_url)
    orchestrator = Orchestrator(task_max_attempts=resolved_settings.task_max_attempts)
    run_service = RunService(database, orchestrator)
    worker = Worker(
        database=database,
        orchestrator=orchestrator,
        provider=provider or FakeProvider(),
        lease_seconds=resolved_settings.worker_lease_seconds,
        timeout_seconds=resolved_settings.task_timeout_seconds,
        poll_interval_seconds=resolved_settings.worker_poll_interval_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if resolved_settings.create_schema_on_start:
            await database.create_schema()

        stop_event = asyncio.Event()
        worker_task: asyncio.Task[None] | None = None
        if resolved_settings.worker_enabled:
            worker_task = asyncio.create_task(worker.run_forever(stop_event))

        try:
            yield
        finally:
            stop_event.set()
            if worker_task is not None:
                await worker_task
            await database.close()

    app = FastAPI(title="Epiphany Studio Runtime", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.database = database
    app.state.orchestrator = orchestrator
    app.state.run_service = run_service
    app.state.worker = worker
    app.include_router(router)
    return app


app = create_app()
