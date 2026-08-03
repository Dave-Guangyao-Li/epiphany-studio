from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from epiphany.db import Database
from epiphany.models import Task
from epiphany.runtime.orchestrator import Orchestrator
from epiphany.runtime.providers import FakeProvider, TaskInvocation
from epiphany.runtime.worker import StaleLease, Worker
from epiphany.services import RunService


async def test_fake_workflow_is_persisted_and_survives_restart(
    runtime: tuple[Database, RunService, Worker],
    database_url: str,
) -> None:
    database, service, worker = runtime
    created = await service.create_run(
        workflow_type="fake-podcast",
        payload={"topic": "成年十年", "sources": ["journal", "voice-note"]},
    )

    assert created.status == "queued"
    assert await worker.run_until_idle() == 3

    completed = await service.get_run(created.id)
    assert completed.status == "succeeded"
    assert completed.current_step == "complete"
    assert completed.model_call_count == 3
    assert len(completed.tasks) == 3
    assert len(completed.artifacts) == 3
    assert all(task.status == "succeeded" for task in completed.tasks)
    assert completed.output_artifact_id == completed.artifacts[-1].id

    events = await service.list_events(created.id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[-1].type == "run.succeeded"

    await database.close()
    restarted_database = Database(database_url)
    restarted_service = RunService(
        restarted_database,
        Orchestrator(task_max_attempts=2),
    )
    after_restart = await restarted_service.get_run(created.id)
    assert after_restart.status == "succeeded"
    assert len(after_restart.tasks) == 3
    await restarted_database.close()


async def test_retry_is_bounded_and_recorded(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    _, service, worker = runtime
    created = await service.create_run(
        workflow_type="fake-podcast",
        payload={
            "topic": "retry",
            "fake_failures": {"prepare_sources": 1},
        },
    )

    assert await worker.run_until_idle() == 4
    completed = await service.get_run(created.id)
    assert completed.status == "succeeded"
    assert completed.tasks[0].attempt == 2
    events = await service.list_events(created.id)
    assert "task.retry_scheduled" in [event.type for event in events]


async def test_retry_exhaustion_fails_run(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    _, service, worker = runtime
    created = await service.create_run(
        workflow_type="fake-podcast",
        payload={
            "topic": "retry exhaustion",
            "fake_failures": {"prepare_sources": 2},
        },
    )

    assert await worker.run_until_idle() == 2
    failed = await service.get_run(created.id)
    assert failed.status == "failed"
    assert failed.tasks[0].status == "failed"
    assert failed.tasks[0].attempt == 2
    events = await service.list_events(created.id)
    assert events[-2].type == "task.failed"
    assert events[-1].type == "run.failed"


async def test_cancelled_run_does_not_execute(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    _, service, worker = runtime
    created = await service.create_run(
        workflow_type="fake-podcast",
        payload={"topic": "cancel"},
    )
    cancelled = await service.cancel_run(created.id)

    assert cancelled.status == "cancelled"
    assert cancelled.tasks[0].status == "cancelled"
    assert await worker.run_until_idle() == 0


async def test_expired_lease_is_recovered_and_old_owner_is_fenced(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    created = await service.create_run(
        workflow_type="fake-podcast",
        payload={"topic": "recovery"},
    )
    old_invocation = await worker.claim_next()
    assert old_invocation is not None

    async with database.sessions() as session, session.begin():
        task = (
            await session.execute(select(Task).where(Task.id == old_invocation.task_id))
        ).scalar_one()
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert await worker.recover_expired() == 1
    new_invocation = await worker.claim_next()
    assert new_invocation is not None
    assert new_invocation.task_id == old_invocation.task_id
    assert new_invocation.lease_token != old_invocation.lease_token

    with pytest.raises(StaleLease):
        await worker.complete(
            old_invocation,
            content={"late": True},
            provider="fake",
            model="fake-v1",
        )

    result = await FakeProvider().generate(new_invocation)
    await worker.complete(
        new_invocation,
        content=result.content,
        provider=result.provider,
        model=result.model,
    )
    assert await worker.run_until_idle() == 2
    completed = await service.get_run(created.id)
    assert completed.status == "succeeded"
    assert completed.tasks[0].attempt == 2


async def test_run_forever_recovers_a_lease_that_expires_after_startup(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    created = await service.create_run(
        workflow_type="fake-podcast",
        payload={"topic": "delayed startup recovery"},
    )
    abandoned_invocation = await worker.claim_next()
    assert abandoned_invocation is not None

    # Simulate a replacement process starting just before the previous
    # process's lease expires. A startup-only recovery scan would miss this.
    async with database.sessions() as session, session.begin():
        task = await session.get(Task, abandoned_invocation.task_id)
        assert task is not None
        task.lease_expires_at = datetime.now(UTC) + timedelta(milliseconds=30)

    worker.poll_interval_seconds = 0.01
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(worker.run_forever(stop_event))
    try:
        for _ in range(200):
            restored = await service.get_run(created.id)
            if restored.status == "succeeded":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("replacement Worker did not recover the later-expiring lease")
    finally:
        stop_event.set()
        await worker_task

    restored = await service.get_run(created.id)
    assert restored.status == "succeeded"
    assert restored.tasks[0].attempt == 2
    assert any(event.type == "task.recovered" for event in await service.list_events(created.id))


async def test_cancel_fences_a_provider_result_that_finishes_in_flight(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    started = asyncio.Event()
    release = asyncio.Event()
    fake = FakeProvider()

    class BlockingProvider:
        name = fake.name
        model = fake.model
        billing_currency = fake.billing_currency

        async def generate(self, invocation: TaskInvocation):
            started.set()
            await release.wait()
            return await fake.generate(invocation)

    worker.provider = BlockingProvider()
    created = await service.create_run(
        workflow_type="fake-podcast",
        payload={"topic": "cancel an in-flight provider"},
    )
    execution = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), timeout=1)

    cancelled = await service.cancel_run(created.id)
    assert cancelled.status == "cancelled"
    release.set()
    assert await asyncio.wait_for(execution, timeout=1) is True

    restored = await service.get_run(created.id)
    assert restored.status == "cancelled"
    assert restored.tasks[0].status == "cancelled"
    assert restored.artifacts == []

    # The external request may still have consumed provider time/cost, but its
    # late result cannot cross the lease fence into product state.
    assert len(restored.model_calls) == 1
    assert restored.model_calls[0].status == "succeeded"
    assert not any(
        event.type == "task.succeeded" for event in await service.list_events(created.id)
    )

    async with database.sessions() as session:
        assert await session.get(Task, restored.tasks[0].id) is not None
