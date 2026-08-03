from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request, Response

from epiphany.api import router
from epiphany.config import Settings
from epiphany.db import Database
from epiphany.ids import new_id
from epiphany.observability import (
    REQUEST_ID_HEADER,
    bind_request_id,
    configure_logging,
    reset_request_id,
)
from epiphany.project_api import router as project_router
from epiphany.project_service import ProjectService
from epiphany.runtime.orchestrator import Orchestrator
from epiphany.runtime.providers import DeepSeekProvider, FakeProvider, ModelProvider
from epiphany.runtime.worker import Worker
from epiphany.services import RunService
from epiphany.source_api import router as source_router
from epiphany.source_service import SourceService

logger = logging.getLogger("epiphany.http")


def _build_deepseek_provider(
    settings: Settings,
    *,
    model: str,
) -> DeepSeekProvider:
    if settings.deepseek_api_key is None:
        raise ValueError("EPIPHANY_MODEL_PROVIDER=deepseek requires EPIPHANY_DEEPSEEK_API_KEY")
    return DeepSeekProvider(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_base_url,
        model=model,
        billing_currency=settings.deepseek_billing_currency,
        max_tokens=settings.deepseek_max_tokens,
        editor_max_tokens=settings.deepseek_editor_max_tokens,
        max_source_chars=settings.deepseek_max_source_chars,
        max_interview_bundle_chars=settings.deepseek_max_interview_bundle_chars,
        max_editor_bundle_chars=settings.deepseek_max_editor_bundle_chars,
        quality_review_max_tokens=settings.deepseek_quality_review_max_tokens,
        max_quality_bundle_chars=settings.deepseek_max_quality_bundle_chars,
        # The Worker owns the total deadline. Give httpx a slightly larger
        # timeout so both layers do not race at the same millisecond.
        request_timeout_seconds=settings.task_timeout_seconds + 5,
    )


def build_provider(settings: Settings) -> ModelProvider:
    if settings.model_provider == "fake":
        return FakeProvider()
    return _build_deepseek_provider(settings, model=settings.deepseek_model)


def build_reviewer_provider(
    settings: Settings,
    *,
    default_provider: ModelProvider,
) -> ModelProvider:
    """Build a trusted Reviewer override without accepting a model from Run input."""

    reviewer_model = settings.deepseek_reviewer_model
    if settings.model_provider != "deepseek" or reviewer_model is None:
        return default_provider
    if reviewer_model == settings.deepseek_model:
        return default_provider
    return _build_deepseek_provider(settings, model=reviewer_model)


def create_app(
    *,
    settings: Settings | None = None,
    provider: ModelProvider | None = None,
    reviewer_provider: ModelProvider | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    database = Database(resolved_settings.database_url)
    orchestrator = Orchestrator(task_max_attempts=resolved_settings.task_max_attempts)
    run_mutation_lock = asyncio.Lock()
    run_service = RunService(database, orchestrator, mutation_lock=run_mutation_lock)
    source_service = SourceService(database)
    project_service = ProjectService(
        database,
        source_service,
        mutation_lock=run_mutation_lock,
    )
    resolved_provider = provider or build_provider(resolved_settings)
    resolved_reviewer_provider = reviewer_provider
    if resolved_reviewer_provider is None and provider is None:
        resolved_reviewer_provider = build_reviewer_provider(
            resolved_settings,
            default_provider=resolved_provider,
        )
    worker = Worker(
        database=database,
        orchestrator=orchestrator,
        provider=resolved_provider,
        reviewer_provider=resolved_reviewer_provider,
        lease_seconds=resolved_settings.worker_lease_seconds,
        timeout_seconds=resolved_settings.task_timeout_seconds,
        poll_interval_seconds=resolved_settings.worker_poll_interval_seconds,
        max_concurrency=resolved_settings.worker_max_concurrency,
        max_model_calls_per_run=resolved_settings.model_max_calls_per_run,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved_settings.log_level)
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
    app.state.source_service = source_service
    app.state.project_service = project_service
    app.state.worker = worker
    app.include_router(router)
    app.include_router(source_router)
    app.include_router(project_router)

    @app.middleware("http")
    async def log_request(request: Request, call_next: object) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_id("req")
        token = bind_request_id(request_id)
        started_at = perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception(
                    "HTTP request failed",
                    extra={
                        "event": "http.request.failed",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                    },
                )
                raise

            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "HTTP request completed",
                extra={
                    "event": "http.request.completed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                },
            )
            return response
        finally:
            reset_request_id(token)

    return app


app = create_app()
