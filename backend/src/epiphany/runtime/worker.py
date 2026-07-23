from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from epiphany.db import Database
from epiphany.events import append_event
from epiphany.ids import new_id
from epiphany.models import Artifact, Run, Task
from epiphany.runtime.orchestrator import Orchestrator
from epiphany.runtime.providers import (
    ModelProvider,
    RetryableProviderError,
    TaskInvocation,
)
from epiphany.state_machine import (
    RunStatus,
    TaskStatus,
    validate_run_transition,
    validate_task_transition,
)


class StaleLease(RuntimeError):
    pass


class Worker:
    def __init__(
        self,
        *,
        database: Database,
        orchestrator: Orchestrator,
        provider: ModelProvider,
        lease_seconds: int,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> None:
        self.database = database
        self.orchestrator = orchestrator
        self.provider = provider
        self.lease_seconds = lease_seconds
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    async def claim_next(self) -> TaskInvocation | None:
        async with self.database.sessions() as session, session.begin():
            task = (
                await session.execute(
                    select(Task)
                    .where(Task.status == TaskStatus.QUEUED)
                    .order_by(Task.created_at, Task.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if task is None:
                return None

            run = await session.get(Run, task.run_id)
            if run is None:
                raise RuntimeError(f"task {task.id} references a missing run")
            if run.status == RunStatus.CANCELLED:
                validate_task_transition(task.status, TaskStatus.CANCELLED)
                task.status = TaskStatus.CANCELLED
                await append_event(
                    session,
                    run_id=run.id,
                    task_id=task.id,
                    event_type="task.cancelled",
                    payload={"reason": "parent_run_cancelled"},
                )
                return None

            if run.status == RunStatus.QUEUED:
                validate_run_transition(run.status, RunStatus.RUNNING)
                run.status = RunStatus.RUNNING
                await append_event(
                    session,
                    run_id=run.id,
                    event_type="run.started",
                    payload={},
                )

            validate_task_transition(task.status, TaskStatus.RUNNING)
            task.status = TaskStatus.RUNNING
            task.attempt += 1
            task.lease_token = new_id("lease")
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            task.error_code = None
            task.error_message = None
            run.current_step = task.kind
            await append_event(
                session,
                run_id=run.id,
                task_id=task.id,
                event_type="task.started",
                payload={"kind": task.kind, "attempt": task.attempt},
            )
            invocation = TaskInvocation(
                task_id=task.id,
                run_id=task.run_id,
                kind=task.kind,
                attempt=task.attempt,
                input_json=task.input_json,
                lease_token=task.lease_token,
            )
        return invocation

    async def complete(
        self,
        invocation: TaskInvocation,
        *,
        content: dict[str, object],
        provider: str,
        model: str,
    ) -> None:
        async with self.database.sessions() as session, session.begin():
            task = await session.get(Task, invocation.task_id)
            if task is None:
                raise StaleLease(f"task {invocation.task_id} no longer exists")
            run = await session.get(Run, invocation.run_id)
            if run is None:
                raise StaleLease(f"run {invocation.run_id} no longer exists")
            if (
                task.status != TaskStatus.RUNNING
                or task.lease_token != invocation.lease_token
                or run.status == RunStatus.CANCELLED
            ):
                raise StaleLease(f"lease no longer owns task {task.id}")

            idempotency_key = f"task-result:{task.id}"
            artifact = (
                await session.execute(
                    select(Artifact).where(Artifact.idempotency_key == idempotency_key)
                )
            ).scalar_one_or_none()
            if artifact is None:
                artifact = Artifact(
                    run_id=run.id,
                    task_id=task.id,
                    kind=f"{task.kind}_result",
                    content_json={
                        **content,
                        "_execution": {
                            "provider": provider,
                            "model": model,
                            "attempt": task.attempt,
                        },
                    },
                    idempotency_key=idempotency_key,
                )
                session.add(artifact)
                await session.flush()

            validate_task_transition(task.status, TaskStatus.SUCCEEDED)
            task.status = TaskStatus.SUCCEEDED
            task.output_artifact_id = artifact.id
            task.lease_token = None
            task.lease_expires_at = None
            run.model_call_count += 1
            await append_event(
                session,
                run_id=run.id,
                task_id=task.id,
                event_type="task.succeeded",
                payload={
                    "kind": task.kind,
                    "attempt": task.attempt,
                    "artifact_id": artifact.id,
                    "provider": provider,
                    "model": model,
                },
            )
            await self.orchestrator.advance_after_success(
                session,
                run=run,
                completed_task=task,
                artifact=artifact,
            )

    async def fail(self, invocation: TaskInvocation, error: Exception) -> None:
        async with self.database.sessions() as session, session.begin():
            task = await session.get(Task, invocation.task_id)
            run = await session.get(Run, invocation.run_id)
            if task is None or run is None:
                return
            if task.status != TaskStatus.RUNNING or task.lease_token != invocation.lease_token:
                return
            if run.status == RunStatus.CANCELLED:
                return

            retryable = isinstance(error, RetryableProviderError)
            task.error_code = getattr(error, "code", "task_execution_error")
            task.error_message = str(error)
            task.lease_token = None
            task.lease_expires_at = None

            if retryable and task.attempt < task.max_attempts:
                validate_task_transition(task.status, TaskStatus.QUEUED)
                task.status = TaskStatus.QUEUED
                await append_event(
                    session,
                    run_id=run.id,
                    task_id=task.id,
                    event_type="task.retry_scheduled",
                    payload={
                        "kind": task.kind,
                        "attempt": task.attempt,
                        "error_code": task.error_code,
                    },
                )
                return

            validate_task_transition(task.status, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED
            validate_run_transition(run.status, RunStatus.FAILED)
            run.status = RunStatus.FAILED
            await append_event(
                session,
                run_id=run.id,
                task_id=task.id,
                event_type="task.failed",
                payload={
                    "kind": task.kind,
                    "attempt": task.attempt,
                    "error_code": task.error_code,
                    "retryable": retryable,
                },
            )
            await append_event(
                session,
                run_id=run.id,
                event_type="run.failed",
                payload={"task_id": task.id, "error_code": task.error_code},
            )

    async def recover_expired(self) -> int:
        recovered = 0
        now = datetime.now(UTC)
        async with self.database.sessions() as session, session.begin():
            tasks = (
                await session.execute(
                    select(Task).where(
                        Task.status == TaskStatus.RUNNING,
                        Task.lease_expires_at.is_not(None),
                        Task.lease_expires_at < now,
                    )
                )
            ).scalars()
            for task in tasks:
                run = await session.get(Run, task.run_id)
                if run is None:
                    continue

                task.lease_token = None
                task.lease_expires_at = None
                if run.status == RunStatus.CANCELLED:
                    validate_task_transition(task.status, TaskStatus.CANCELLED)
                    task.status = TaskStatus.CANCELLED
                    event_type = "task.cancelled"
                elif task.attempt < task.max_attempts:
                    validate_task_transition(task.status, TaskStatus.QUEUED)
                    task.status = TaskStatus.QUEUED
                    event_type = "task.recovered"
                else:
                    validate_task_transition(task.status, TaskStatus.FAILED)
                    task.status = TaskStatus.FAILED
                    task.error_code = "lease_expired"
                    task.error_message = "task lease expired and attempts were exhausted"
                    if run.status == RunStatus.RUNNING:
                        validate_run_transition(run.status, RunStatus.FAILED)
                        run.status = RunStatus.FAILED
                    event_type = "task.failed"

                await append_event(
                    session,
                    run_id=run.id,
                    task_id=task.id,
                    event_type=event_type,
                    payload={"kind": task.kind, "reason": "lease_expired"},
                )
                if event_type == "task.failed":
                    await append_event(
                        session,
                        run_id=run.id,
                        event_type="run.failed",
                        payload={"task_id": task.id, "error_code": task.error_code},
                    )
                recovered += 1
        return recovered

    async def run_once(self) -> bool:
        invocation = await self.claim_next()
        if invocation is None:
            return False
        try:
            result = await asyncio.wait_for(
                self.provider.generate(invocation),
                timeout=self.timeout_seconds,
            )
            await self.complete(
                invocation,
                content=result.content,
                provider=result.provider,
                model=result.model,
            )
        except StaleLease:
            pass
        except TimeoutError as error:
            await self.fail(
                invocation,
                RetryableProviderError(f"task timed out: {error}"),
            )
        except Exception as error:  # Worker boundary persists the error before continuing.
            await self.fail(invocation, error)
        return True

    async def run_until_idle(self, *, max_tasks: int = 100) -> int:
        processed = 0
        while processed < max_tasks and await self.run_once():
            processed += 1
        return processed

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        await self.recover_expired()
        while not stop_event.is_set():
            if await self.run_once():
                continue
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except TimeoutError:
                continue
