from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import perf_counter

from pydantic import ValidationError
from sqlalchemy import select

from epiphany.db import Database
from epiphany.draft_quality_schemas import (
    REVIEW_PODCAST_DRAFT,
    ModelSelfReviewOutputError,
)
from epiphany.events import append_event
from epiphany.ids import new_id
from epiphany.models import Artifact, Run, Task
from epiphany.research_schemas import (
    THEME_RESEARCH,
    TIMELINE_RESEARCH,
    QuoteSourceMismatch,
)
from epiphany.revision_schemas import (
    REVISE_PODCAST_DRAFT,
    PodcastRevisionOutputError,
)
from epiphany.runtime.model_call_ledger import ModelCallLeaseLost, ModelCallLedger
from epiphany.runtime.orchestrator import Orchestrator
from epiphany.runtime.output_validation import validate_task_output
from epiphany.runtime.providers import (
    ModelProvider,
    ProviderOutputTruncatedError,
    ProviderTimeoutError,
    RetryableProviderError,
    TaskInvocation,
)
from epiphany.source_starter_schemas import (
    BUILD_SOURCE_STARTER,
    SourceStarterOutputValidationError,
    build_safe_source_starter_candidate,
    ground_source_starter_candidate,
)
from epiphany.state_machine import (
    RunStatus,
    TaskStatus,
    validate_run_transition,
    validate_task_transition,
)
from epiphany.supplemental_interview_schemas import (
    PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW,
)

logger = logging.getLogger("epiphany.worker")


class StaleLease(RuntimeError):
    pass


class SanitizedTaskOutputError(ValueError):
    """Persistable validation failure that never includes model-returned values."""

    def __init__(self, *, code: str) -> None:
        self.code = code
        super().__init__(f"model output failed strict validation ({code})")


class RetryableSanitizedTaskOutputError(RetryableProviderError):
    """A bounded model-output repair that preserves only a safe rule code."""

    def __init__(self, *, code: str) -> None:
        self.code = code
        super().__init__(f"model output failed strict validation ({code})")


def _sanitized_task_output_error(
    error: Exception,
    *,
    retryable: bool = False,
) -> SanitizedTaskOutputError | RetryableSanitizedTaskOutputError:
    raw_code = getattr(error, "code", "task_output_invalid")
    code = raw_code if isinstance(raw_code, str) else "task_output_invalid"
    if (
        not code
        or len(code) > 80
        or any(character != "_" and not character.isalnum() for character in code)
    ):
        code = "task_output_invalid"
    error_type = RetryableSanitizedTaskOutputError if retryable else SanitizedTaskOutputError
    return error_type(code=code)


def _is_repairable_source_starter_output_error(
    *,
    task_kind: str,
    error: Exception,
) -> bool:
    """Identify only strict Source Starter output-contract failures.

    ``SourceStarterOutputValidationError`` covers the custom grounding and
    safety rules that run after schema parsing. ``ValidationError`` covers the
    Pydantic contract itself (missing fields, forbidden extra fields, wrong
    literals, and so on). Both describe a successfully returned model payload
    that the product cannot safely expose, so the Source Starter gets one
    bounded repair and then a server-owned fallback.

    Provider/network failures happen before this boundary and deliberately do
    not match. Programming errors and a missing validator also remain fatal
    instead of being hidden behind the fallback.
    """

    return task_kind == BUILD_SOURCE_STARTER and isinstance(
        error,
        (SourceStarterOutputValidationError, ValidationError),
    )


def _is_repairable_revision_output_error(
    *,
    task_kind: str,
    error: Exception,
) -> bool:
    """Give one strict Revision candidate a bounded repair attempt.

    A valid hosted-model response can still ignore the explicit edit and return
    the immutable parent Draft.  That is a model-output contract failure, not a
    provider/network failure and not a reason to weaken grounding.  Revision
    Tasks already have two attempts; the second prompt is an explicit repair,
    after which the normal failure boundary remains intact.
    """

    return task_kind == REVISE_PODCAST_DRAFT and isinstance(
        error,
        PodcastRevisionOutputError,
    )


def _is_repairable_quality_review_output_error(
    *,
    task_kind: str,
    error: Exception,
) -> bool:
    """Allow one bounded repair without weakening Reviewer evidence rules.

    Schema, verbatim quote, reference-scope, and writing-style evidence errors
    all belong to ``ModelSelfReviewOutputError``.  The second model call gets a
    repair-specific prompt.  If that output is still invalid, the existing
    advisory-Reviewer degradation path preserves the Draft and deterministic
    quality report.
    """

    return task_kind == REVIEW_PODCAST_DRAFT and isinstance(
        error,
        ModelSelfReviewOutputError,
    )


class Worker:
    def __init__(
        self,
        *,
        database: Database,
        orchestrator: Orchestrator,
        provider: ModelProvider,
        reviewer_provider: ModelProvider | None = None,
        lease_seconds: int,
        timeout_seconds: float,
        poll_interval_seconds: float,
        max_concurrency: int = 2,
        max_model_calls_per_run: int = 6,
    ) -> None:
        self.database = database
        self.orchestrator = orchestrator
        self.provider = provider
        self.reviewer_provider = reviewer_provider
        self.lease_seconds = lease_seconds
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.max_concurrency = max_concurrency
        self._finalization_lock = asyncio.Lock()
        self.model_call_ledger = ModelCallLedger(
            database,
            max_calls_per_run=max_model_calls_per_run,
        )

    @property
    def max_model_calls_per_run(self) -> int:
        return self.model_call_ledger.max_calls_per_run

    @max_model_calls_per_run.setter
    def max_model_calls_per_run(self, value: int) -> None:
        self.model_call_ledger.max_calls_per_run = value

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
                logger.info(
                    "Skipped task because parent Run is cancelled",
                    extra={
                        "event": "worker.task.skipped",
                        "run_id": run.id,
                        "task_id": task.id,
                        "task_kind": task.kind,
                    },
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
            previous_error_code = task.error_code
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
                previous_error_code=previous_error_code,
            )
        logger.info(
            "Worker claimed task",
            extra={
                "event": "worker.task.claimed",
                "run_id": invocation.run_id,
                "task_id": invocation.task_id,
                "task_kind": invocation.kind,
                "attempt": invocation.attempt,
            },
        )
        return invocation

    async def complete(
        self,
        invocation: TaskInvocation,
        *,
        content: dict[str, object],
        provider: str,
        model: str,
        execution_metadata: dict[str, object] | None = None,
    ) -> None:
        async with self._finalization_lock:
            await self._complete(
                invocation,
                content=content,
                provider=provider,
                model=model,
                execution_metadata=execution_metadata,
            )

    async def _complete(
        self,
        invocation: TaskInvocation,
        *,
        content: dict[str, object],
        provider: str,
        model: str,
        execution_metadata: dict[str, object] | None = None,
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
                or run.status != RunStatus.RUNNING
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
                    kind=(
                        "source_starter_candidate"
                        if task.kind == BUILD_SOURCE_STARTER
                        else f"{task.kind}_result"
                    ),
                    content_json={
                        **content,
                        "_execution": {
                            "provider": provider,
                            "model": model,
                            "attempt": task.attempt,
                            **(execution_metadata or {}),
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
            artifact_id = artifact.id

        logger.info(
            "Worker completed task",
            extra={
                "event": "worker.task.completed",
                "run_id": invocation.run_id,
                "task_id": invocation.task_id,
                "task_kind": invocation.kind,
                "attempt": invocation.attempt,
                "artifact_id": artifact_id,
            },
        )

    async def fail(self, invocation: TaskInvocation, error: Exception) -> None:
        async with self._finalization_lock:
            await self._fail(invocation, error)

    async def _fail(self, invocation: TaskInvocation, error: Exception) -> None:
        async with self.database.sessions() as session, session.begin():
            task = await session.get(Task, invocation.task_id)
            run = await session.get(Run, invocation.run_id)
            if task is None or run is None:
                return
            if task.status != TaskStatus.RUNNING or task.lease_token != invocation.lease_token:
                return
            if run.status == RunStatus.CANCELLED:
                return

            research_output_truncated = isinstance(
                error, ProviderOutputTruncatedError
            ) and task.kind in {TIMELINE_RESEARCH, THEME_RESEARCH}
            retryable = isinstance(error, RetryableProviderError) or research_output_truncated
            retry_limit = (
                min(task.max_attempts, 2) if research_output_truncated else task.max_attempts
            )
            task.error_code = getattr(error, "code", "task_execution_error")
            task.error_message = str(error)
            task.lease_token = None
            task.lease_expires_at = None

            if retryable and task.attempt < retry_limit:
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
                logger.warning(
                    "Worker scheduled task retry",
                    extra={
                        "event": "worker.task.retry_scheduled",
                        "run_id": run.id,
                        "task_id": task.id,
                        "task_kind": task.kind,
                        "attempt": task.attempt,
                        "error_code": task.error_code,
                    },
                )
                return

            validate_task_transition(task.status, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED
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
            await self.orchestrator.fail_after_task(
                session,
                run=run,
                failed_task=task,
            )
            logger.error(
                "Worker failed task",
                extra={
                    "event": "worker.task.failed",
                    "run_id": run.id,
                    "task_id": task.id,
                    "task_kind": task.kind,
                    "attempt": task.attempt,
                    "error_code": task.error_code,
                },
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
                await self.model_call_ledger.abandon_expired(
                    session,
                    task=task,
                    run=run,
                    now=now,
                )
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
                    event_type = "task.failed"

                await append_event(
                    session,
                    run_id=run.id,
                    task_id=task.id,
                    event_type=event_type,
                    payload={"kind": task.kind, "reason": "lease_expired"},
                )
                if event_type == "task.failed":
                    await self.orchestrator.fail_after_task(
                        session,
                        run=run,
                        failed_task=task,
                    )
                recovered += 1
        if recovered:
            logger.warning(
                "Worker recovered expired task leases",
                extra={
                    "event": "worker.tasks.recovered",
                    "recovered_count": recovered,
                },
            )
        return recovered

    async def run_once(self) -> bool:
        invocation = await self.claim_next()
        if invocation is None:
            return False
        await self._execute_invocation(invocation)
        return True

    async def _execute_invocation(self, invocation: TaskInvocation) -> None:
        provider = self._provider_for(invocation)
        try:
            model_call_id = await self.model_call_ledger.reserve(
                invocation,
                provider=provider.name,
                model=provider.model,
                cost_currency=provider.billing_currency,
            )
        except ModelCallLeaseLost:
            logger.warning(
                "Worker rejected stale task before provider call",
                extra={
                    "event": "worker.task.stale_result",
                    "run_id": invocation.run_id,
                    "task_id": invocation.task_id,
                    "task_kind": invocation.kind,
                    "attempt": invocation.attempt,
                },
            )
            return
        except Exception as error:
            await self.fail(invocation, error)
            return

        started_at = perf_counter()
        try:
            result = await asyncio.wait_for(
                provider.generate(invocation),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            duration_ms = max(0, round((perf_counter() - started_at) * 1000))
            error = ProviderTimeoutError(f"provider call exceeded {self.timeout_seconds} seconds")
            await self.model_call_ledger.finish(
                model_call_id,
                invocation,
                status="timed_out",
                duration_ms=duration_ms,
                result=error.accounting_result,
                error_code=error.code,
            )
            await self.fail(invocation, error)
            return
        except ProviderTimeoutError as error:
            duration_ms = max(0, round((perf_counter() - started_at) * 1000))
            await self.model_call_ledger.finish(
                model_call_id,
                invocation,
                status="timed_out",
                duration_ms=duration_ms,
                result=error.accounting_result,
                error_code=error.code,
            )
            await self.fail(invocation, error)
            return
        except Exception as error:
            duration_ms = max(0, round((perf_counter() - started_at) * 1000))
            await self.model_call_ledger.finish(
                model_call_id,
                invocation,
                status="failed",
                duration_ms=duration_ms,
                result=getattr(error, "accounting_result", None),
                error_code=getattr(error, "code", "provider_error"),
            )
            await self.fail(invocation, error)
            return

        duration_ms = max(0, round((perf_counter() - started_at) * 1000))
        await self.model_call_ledger.finish(
            model_call_id,
            invocation,
            status="succeeded",
            duration_ms=duration_ms,
            result=result,
        )
        execution_metadata: dict[str, object] | None = None
        try:
            validated_content = validate_task_output(
                task_kind=invocation.kind,
                task_input=invocation.input_json,
                content=result.content,
            )
        except Exception as error:
            source_starter_output_error = _is_repairable_source_starter_output_error(
                task_kind=invocation.kind,
                error=error,
            )
            revision_output_error = _is_repairable_revision_output_error(
                task_kind=invocation.kind,
                error=error,
            )
            quality_review_output_error = _is_repairable_quality_review_output_error(
                task_kind=invocation.kind,
                error=error,
            )
            if source_starter_output_error and invocation.attempt > 1:
                # The hosted model has already had one bounded repair attempt.
                # A common live failure is useful brainstorming expressed as
                # an unsupported ``我……`` assertion. Preserve the rest of that
                # candidate and make only those lines visibly provisional.
                # Every other contract failure still uses the fully
                # server-owned safe template.
                validation_error_code = _sanitized_task_output_error(error).code
                fallback_kind = "server_safe_template"
                fallback_content: dict[str, object]
                if isinstance(error, SourceStarterOutputValidationError):
                    try:
                        fallback_content = ground_source_starter_candidate(
                            task_input=invocation.input_json,
                            content=result.content,
                        )
                        # Do not trust the local transformation by itself. It
                        # must pass the same complete output contract as a
                        # hosted-model response.
                        validate_task_output(
                            task_kind=invocation.kind,
                            task_input=invocation.input_json,
                            content=fallback_content,
                        )
                        fallback_kind = "server_line_grounding"
                    except (SourceStarterOutputValidationError, ValidationError):
                        fallback_content = build_safe_source_starter_candidate(
                            task_input=invocation.input_json
                        )
                else:
                    fallback_content = build_safe_source_starter_candidate(
                        task_input=invocation.input_json
                    )
                validated_content = validate_task_output(
                    task_kind=invocation.kind,
                    task_input=invocation.input_json,
                    content=fallback_content,
                )
                execution_metadata = {
                    "fallback": fallback_kind,
                    "model_output_validation_error": validation_error_code,
                }
                logger.warning(
                    "Worker repaired invalid Source Starter output with a bounded fallback",
                    extra={
                        "event": "worker.source_starter.safe_fallback",
                        "run_id": invocation.run_id,
                        "task_id": invocation.task_id,
                        "task_kind": invocation.kind,
                        "attempt": invocation.attempt,
                        "error_code": execution_metadata["model_output_validation_error"],
                    },
                )
            else:
                await self.fail(
                    invocation,
                    _sanitized_task_output_error(
                        error,
                        retryable=(
                            source_starter_output_error
                            or revision_output_error
                            or quality_review_output_error
                            or (
                                invocation.kind == THEME_RESEARCH
                                and isinstance(error, QuoteSourceMismatch)
                            )
                        ),
                    ),
                )
                return

        try:
            await self.complete(
                invocation,
                content=validated_content,
                provider=result.provider,
                model=result.model,
                execution_metadata=execution_metadata,
            )
        except StaleLease:
            logger.warning(
                "Worker rejected stale task result",
                extra={
                    "event": "worker.task.stale_result",
                    "run_id": invocation.run_id,
                    "task_id": invocation.task_id,
                    "task_kind": invocation.kind,
                    "attempt": invocation.attempt,
                },
            )
        except Exception as error:  # Worker boundary persists the error before continuing.
            await self.fail(invocation, error)

    def _provider_for(self, invocation: TaskInvocation) -> ModelProvider:
        if invocation.kind == REVIEW_PODCAST_DRAFT and self.reviewer_provider is not None:
            return self.reviewer_provider
        if invocation.kind == PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW:
            # The supplemental Interviewer is a generation Agent, not the
            # advisory Reviewer. It deliberately uses the main provider.
            return self.provider
        return self.provider

    async def run_batch(self, *, limit: int | None = None) -> int:
        batch_limit = min(limit or self.max_concurrency, self.max_concurrency)
        invocations: list[TaskInvocation] = []
        for _ in range(batch_limit):
            invocation = await self.claim_next()
            if invocation is None:
                break
            invocations.append(invocation)

        if not invocations:
            return 0

        logger.info(
            "Worker executing task batch",
            extra={
                "event": "worker.batch.started",
                "concurrency": len(invocations),
            },
        )
        await asyncio.gather(*(self._execute_invocation(invocation) for invocation in invocations))
        return len(invocations)

    async def run_until_idle(self, *, max_tasks: int = 100) -> int:
        processed = 0
        while processed < max_tasks:
            batch_size = await self.run_batch(limit=max_tasks - processed)
            if batch_size == 0:
                break
            processed += batch_size
        return processed

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        logger.info("Worker started", extra={"event": "worker.started"})
        try:
            while not stop_event.is_set():
                # Recovery is deliberately periodic instead of startup-only.
                # A replacement Worker can start while an old lease is still
                # valid; that lease may expire a few seconds later and must not
                # leave the durable Task stuck in ``running`` forever.
                await self.recover_expired()
                if await self.run_batch():
                    continue
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.poll_interval_seconds,
                    )
                except TimeoutError:
                    continue
        finally:
            logger.info("Worker stopped", extra={"event": "worker.stopped"})
