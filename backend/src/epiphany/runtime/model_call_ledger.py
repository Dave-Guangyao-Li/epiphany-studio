from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from epiphany.db import Database
from epiphany.events import append_event
from epiphany.models import ModelCall, Run, Task
from epiphany.runtime.providers import (
    ModelCallLimitExceeded,
    ProviderResult,
    TaskInvocation,
)
from epiphany.state_machine import RunStatus, TaskStatus

logger = logging.getLogger("epiphany.model_call")


class ModelCallLeaseLost(RuntimeError):
    pass


class ModelCallLedger:
    """Durable budget and accounting boundary around Provider calls."""

    def __init__(self, database: Database, *, max_calls_per_run: int) -> None:
        self.database = database
        self.max_calls_per_run = max_calls_per_run
        self._reservation_lock = asyncio.Lock()

    async def reserve(
        self,
        invocation: TaskInvocation,
        *,
        provider: str,
        model: str,
    ) -> str:
        limit_exceeded = False
        call_id: str | None = None
        async with self._reservation_lock:
            async with self.database.sessions() as session, session.begin():
                existing = (
                    await session.execute(
                        select(ModelCall).where(
                            ModelCall.task_id == invocation.task_id,
                            ModelCall.attempt == invocation.attempt,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    raise RuntimeError(
                        f"model call already reserved for task {invocation.task_id} "
                        f"attempt {invocation.attempt}"
                    )

                run = await session.get(Run, invocation.run_id)
                task = await session.get(Task, invocation.task_id)
                if (
                    run is None
                    or task is None
                    or task.status != TaskStatus.RUNNING
                    or task.lease_token != invocation.lease_token
                    or run.status != RunStatus.RUNNING
                ):
                    raise ModelCallLeaseLost(
                        f"lease no longer permits a provider call for task {invocation.task_id}"
                    )

                current_count = (
                    await session.execute(
                        select(func.count(ModelCall.id)).where(
                            ModelCall.run_id == invocation.run_id
                        )
                    )
                ).scalar_one()
                if current_count >= self.max_calls_per_run:
                    limit_exceeded = True
                    await append_event(
                        session,
                        run_id=invocation.run_id,
                        task_id=invocation.task_id,
                        event_type="model.call.limit_exceeded",
                        payload={
                            "attempt": invocation.attempt,
                            "limit": self.max_calls_per_run,
                        },
                    )
                else:
                    model_call = ModelCall(
                        run_id=invocation.run_id,
                        task_id=invocation.task_id,
                        attempt=invocation.attempt,
                        provider=provider,
                        model=model,
                        status="started",
                    )
                    session.add(model_call)
                    run.model_call_count += 1
                    await session.flush()
                    await append_event(
                        session,
                        run_id=invocation.run_id,
                        task_id=invocation.task_id,
                        event_type="model.call.started",
                        payload={
                            "model_call_id": model_call.id,
                            "attempt": invocation.attempt,
                            "provider": model_call.provider,
                            "model": model_call.model,
                        },
                    )
                    call_id = model_call.id

        if limit_exceeded:
            logger.warning(
                "Model call limit exceeded",
                extra={
                    "event": "model.call.limit_exceeded",
                    "run_id": invocation.run_id,
                    "task_id": invocation.task_id,
                    "task_kind": invocation.kind,
                    "attempt": invocation.attempt,
                    "limit": self.max_calls_per_run,
                },
            )
            raise ModelCallLimitExceeded(
                f"run exceeded its {self.max_calls_per_run} model call limit"
            )

        if call_id is None:
            raise RuntimeError("model call reservation did not produce an id")
        logger.info(
            "Model call started",
            extra={
                "event": "model.call.started",
                "run_id": invocation.run_id,
                "task_id": invocation.task_id,
                "task_kind": invocation.kind,
                "attempt": invocation.attempt,
                "model_call_id": call_id,
                "provider": provider,
                "model": model,
            },
        )
        return call_id

    async def finish(
        self,
        call_id: str,
        invocation: TaskInvocation,
        *,
        status: str,
        duration_ms: int,
        result: ProviderResult | None = None,
        error_code: str | None = None,
    ) -> None:
        async with self.database.sessions() as session, session.begin():
            model_call = await session.get(ModelCall, call_id)
            if model_call is None or model_call.status != "started":
                return
            run = await session.get(Run, invocation.run_id)

            model_call.status = status
            model_call.duration_ms = duration_ms
            model_call.completed_at = datetime.now(UTC)
            model_call.error_code = error_code
            if result is not None:
                model_call.provider = result.provider
                model_call.model = result.model
                model_call.input_tokens = result.input_tokens
                model_call.output_tokens = result.output_tokens
                model_call.estimated_cost_micros = result.estimated_cost_micros
                model_call.cost_currency = result.cost_currency.upper()

            if run is not None and run.status == RunStatus.RUNNING:
                event_type = (
                    "model.call.completed" if status == "succeeded" else "model.call.failed"
                )
                await append_event(
                    session,
                    run_id=invocation.run_id,
                    task_id=invocation.task_id,
                    event_type=event_type,
                    payload=self._event_payload(model_call),
                )

        log = logger.info if status == "succeeded" else logger.warning
        log(
            "Model call completed",
            extra={
                "event": ("model.call.completed" if status == "succeeded" else "model.call.failed"),
                "run_id": invocation.run_id,
                "task_id": invocation.task_id,
                "task_kind": invocation.kind,
                "attempt": invocation.attempt,
                "model_call_id": call_id,
                "status": status,
                "duration_ms": duration_ms,
                "error_code": error_code,
            },
        )

    async def abandon_expired(
        self,
        session: AsyncSession,
        *,
        task: Task,
        run: Run,
        now: datetime,
    ) -> None:
        model_call = (
            await session.execute(
                select(ModelCall).where(
                    ModelCall.task_id == task.id,
                    ModelCall.attempt == task.attempt,
                    ModelCall.status == "started",
                )
            )
        ).scalar_one_or_none()
        if model_call is None:
            return

        call_started_at = model_call.started_at
        if call_started_at.tzinfo is None:
            call_started_at = call_started_at.replace(tzinfo=UTC)
        model_call.status = "failed"
        model_call.error_code = "lease_expired"
        model_call.completed_at = now
        model_call.duration_ms = max(
            0,
            int((now - call_started_at).total_seconds() * 1000),
        )
        await append_event(
            session,
            run_id=run.id,
            task_id=task.id,
            event_type="model.call.failed",
            payload=self._event_payload(model_call),
        )

    @staticmethod
    def _event_payload(model_call: ModelCall) -> dict[str, object]:
        return {
            "model_call_id": model_call.id,
            "attempt": model_call.attempt,
            "provider": model_call.provider,
            "model": model_call.model,
            "status": model_call.status,
            "input_tokens": model_call.input_tokens,
            "output_tokens": model_call.output_tokens,
            "duration_ms": model_call.duration_ms,
            "estimated_cost_micros": model_call.estimated_cost_micros,
            "cost_currency": model_call.cost_currency,
            "error_code": model_call.error_code,
        }
