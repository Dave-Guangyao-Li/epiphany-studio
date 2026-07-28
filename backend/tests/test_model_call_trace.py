from __future__ import annotations

import asyncio

from epiphany.db import Database
from epiphany.runtime.providers import FakeProvider, ProviderResult, TaskInvocation
from epiphany.runtime.worker import Worker
from epiphany.services import RunService
from epiphany.source_service import SourceService


async def _create_research_run(
    database: Database,
    service: RunService,
) -> str:
    imported = await SourceService(database).import_text(
        title="Model call accounting fixture",
        source_type="podcast_draft",
        text="这是一段不发送到网络的合成测试素材。",
        metadata={},
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "模型调用记账",
            "source_ids": [imported.source.id],
        },
    )
    return created.id


class UsageFakeProvider(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        await asyncio.sleep(0.01)
        result = await super().generate(invocation)
        return ProviderResult(
            content=result.content,
            provider="fake-usage",
            model="fake-usage-v1",
            input_tokens=120,
            output_tokens=30,
            estimated_cost_micros=17,
            cost_currency="usd",
        )


async def test_usage_latency_and_cost_are_persisted_without_network(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    worker.provider = UsageFakeProvider()
    run_id = await _create_research_run(database, service)

    assert await worker.run_until_idle() == 3
    completed = await service.get_run(run_id)

    assert completed.status == "succeeded"
    assert completed.model_call_count == 3
    assert len(completed.model_calls) == 3
    assert all(call.status == "succeeded" for call in completed.model_calls)
    assert all(call.provider == "fake-usage" for call in completed.model_calls)
    assert all(call.model == "fake-usage-v1" for call in completed.model_calls)
    assert sum(call.input_tokens for call in completed.model_calls) == 360
    assert sum(call.output_tokens for call in completed.model_calls) == 90
    assert sum(call.estimated_cost_micros for call in completed.model_calls) == 51
    assert all(call.cost_currency == "USD" for call in completed.model_calls)
    assert all(
        call.duration_ms is not None and call.duration_ms >= 1 for call in completed.model_calls
    )

    events = await service.list_events(run_id)
    event_types = [event.type for event in events]
    assert event_types.count("model.call.started") == 3
    assert event_types.count("model.call.completed") == 3


async def test_retry_attempts_are_each_accounted_for(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    _, service, worker = runtime
    created = await service.create_run(
        workflow_type="fake-podcast",
        payload={
            "topic": "retry accounting",
            "fake_failures": {"prepare_sources": 1},
        },
    )

    assert await worker.run_until_idle() == 4
    completed = await service.get_run(created.id)

    assert completed.status == "succeeded"
    assert completed.model_call_count == 4
    assert len(completed.model_calls) == 4
    assert [call.status for call in completed.model_calls].count("failed") == 1
    assert [call.status for call in completed.model_calls].count("succeeded") == 3
    first_task_calls = [
        call for call in completed.model_calls if call.task_id == completed.tasks[0].id
    ]
    assert [call.attempt for call in first_task_calls] == [1, 2]


class CountingFakeProvider(FakeProvider):
    def __init__(self) -> None:
        self.invocations = 0

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        self.invocations += 1
        return await super().generate(invocation)


async def test_call_limit_stops_before_an_extra_provider_invocation(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    _, service, worker = runtime
    provider = CountingFakeProvider()
    worker.provider = provider
    worker.max_model_calls_per_run = 1
    created = await service.create_run(
        workflow_type="fake-podcast",
        payload={"topic": "bounded calls"},
    )

    assert await worker.run_until_idle() == 2
    failed = await service.get_run(created.id)

    assert failed.status == "failed"
    assert failed.model_call_count == 1
    assert len(failed.model_calls) == 1
    assert provider.invocations == 1
    assert failed.tasks[1].error_code == "model_call_limit_exceeded"
    events = await service.list_events(created.id)
    assert "model.call.limit_exceeded" in [event.type for event in events]


class SlowFakeProvider(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        await asyncio.sleep(0.03)
        return await super().generate(invocation)


async def test_timeout_is_retryable_and_each_attempt_is_traced(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    _, service, worker = runtime
    worker.provider = SlowFakeProvider()
    worker.timeout_seconds = 0.005
    created = await service.create_run(
        workflow_type="fake-podcast",
        payload={"topic": "timeout trace"},
    )

    assert await worker.run_until_idle() == 2
    failed = await service.get_run(created.id)

    assert failed.status == "failed"
    assert failed.tasks[0].attempt == 2
    assert failed.tasks[0].error_code == "provider_timeout"
    assert failed.model_call_count == 2
    assert len(failed.model_calls) == 2
    assert all(call.status == "timed_out" for call in failed.model_calls)
    assert all(call.error_code == "provider_timeout" for call in failed.model_calls)
