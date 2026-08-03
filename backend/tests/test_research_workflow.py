from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from epiphany.config import Settings
from epiphany.db import Database
from epiphany.main import create_app
from epiphany.models import Run, Task
from epiphany.runtime.providers import FakeProvider, ProviderResult, TaskInvocation
from epiphany.runtime.worker import Worker
from epiphany.services import RunService
from epiphany.source_service import SourceService


async def _import_source(database: Database) -> str:
    imported = await SourceService(database).import_text(
        title="EP0 口播素材",
        source_type="podcast_draft",
        text=(
            "2019年第一次记录项目，2024年重新整理旧笔记。\n\n测试材料中的第二段只用于验证稳定引用。"
        ),
        metadata={"episode": 0},
    )
    return imported.source.id


async def test_episode_research_fans_out_and_fans_in(
    runtime: tuple[Database, RunService, Worker],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="epiphany.orchestrator")
    database, service, worker = runtime
    source_id = await _import_source(database)

    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始录播客",
            "source_ids": [source_id],
        },
    )

    assert created.status == "running"
    assert created.current_step == "research_parallel"
    assert len(created.tasks) == 3
    manager = next(task for task in created.tasks if task.kind == "research_manager")
    children = [task for task in created.tasks if task.parent_task_id == manager.id]
    assert {task.kind for task in children} == {
        "timeline_research",
        "theme_research",
    }
    assert all(task.status == "queued" for task in children)
    async with database.sessions() as session:
        child_rows = (
            await session.execute(select(Task).where(Task.id.in_([task.id for task in children])))
        ).scalars()
        assert all(task.input_json["topic"] == "五年后重新开始录播客" for task in child_rows)

    assert await worker.run_until_idle() == 3

    completed = await service.get_run(created.id)
    assert completed.workflow_version == "v4"
    assert completed.status == "waiting_for_user"
    assert completed.current_step == "awaiting_interview_response"
    assert completed.model_call_count == 3
    assert len(completed.tasks) == 4
    assert len(completed.artifacts) == 4
    assert {artifact.kind for artifact in completed.artifacts} == {
        "timeline_research_result",
        "theme_research_result",
        "episode_research_bundle",
        "build_interview_scaffold_result",
    }
    assert (
        next(task for task in completed.tasks if task.kind == "research_manager").status
        == "succeeded"
    )
    scaffold_task = next(
        task for task in completed.tasks if task.kind == "build_interview_scaffold"
    )
    assert scaffold_task.parent_task_id is None
    assert scaffold_task.status == "succeeded"
    assert all(task.status not in {"queued", "running"} for task in completed.tasks)
    assert completed.output_artifact_id == next(
        artifact.id
        for artifact in completed.artifacts
        if artifact.kind == "build_interview_scaffold_result"
    )

    events = await service.list_events(created.id)
    event_types = [event.type for event in events]
    assert "workflow.fan_out.started" in event_types
    assert "workflow.fan_in.waiting" in event_types
    assert "workflow.fan_in.completed" in event_types
    assert "workflow.interview_scaffold.queued" in event_types
    assert "workflow.interview_scaffold.completed" in event_types
    assert event_types.index("workflow.fan_in.completed") < event_types.index(
        "workflow.interview_scaffold.queued"
    )
    assert event_types.index("workflow.interview_scaffold.queued") < event_types.index(
        "workflow.interview_scaffold.completed"
    )
    assert event_types.index("workflow.interview_scaffold.completed") < event_types.index(
        "workflow.user_input.requested"
    )
    assert event_types.index("workflow.user_input.requested") < event_types.index(
        "run.waiting_for_user"
    )
    assert "run.succeeded" not in event_types
    assert event_types[-1] == "run.waiting_for_user"

    waiting_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "run.waiting_for_user"
    ]
    assert len(waiting_records) == 1
    waiting_record = waiting_records[0]
    assert waiting_record.run_id == completed.id
    assert waiting_record.task_id == scaffold_task.id
    assert waiting_record.artifact_id == completed.output_artifact_id
    assert waiting_record.checkpoint == "interview_scaffold"
    assert waiting_record.section_count == 3
    assert waiting_record.question_count == 6


async def test_in_flight_v1_research_run_finishes_without_new_topic_or_scaffold(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    source_id = await _import_source(database)
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "temporary v2 topic",
            "source_ids": [source_id],
        },
    )
    async with database.sessions() as session, session.begin():
        run = await session.get(Run, created.id)
        assert run is not None
        run.workflow_version = "v1"
        run.input_json = {"source_ids": [source_id]}

    assert await worker.run_until_idle() == 2
    completed = await service.get_run(created.id)

    assert completed.status == "succeeded"
    assert completed.workflow_version == "v1"
    assert completed.model_call_count == 2
    assert {artifact.kind for artifact in completed.artifacts} == {
        "timeline_research_result",
        "theme_research_result",
        "episode_research_bundle",
    }
    assert completed.output_artifact_id == next(
        artifact.id
        for artifact in completed.artifacts
        if artifact.kind == "episode_research_bundle"
    )
    assert all(task.kind != "build_interview_scaffold" for task in completed.tasks)


async def test_in_flight_v2_research_run_finishes_at_interview_scaffold(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    source_id = await _import_source(database)
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始录播客",
            "source_ids": [source_id],
        },
    )
    async with database.sessions() as session, session.begin():
        run = await session.get(Run, created.id)
        assert run is not None
        run.workflow_version = "v2"

    assert await worker.run_until_idle() == 3
    completed = await service.get_run(created.id)

    assert completed.workflow_version == "v2"
    assert completed.status == "succeeded"
    assert completed.current_step == "complete"
    assert completed.model_call_count == 3
    assert len(completed.tasks) == 4
    assert len(completed.artifacts) == 4
    assert completed.output_artifact_id == next(
        artifact.id
        for artifact in completed.artifacts
        if artifact.kind == "build_interview_scaffold_result"
    )
    assert all(task.status not in {"queued", "running"} for task in completed.tasks)

    events = await service.list_events(created.id)
    event_types = [event.type for event in events]
    assert "workflow.interview_scaffold.completed" in event_types
    assert "workflow.user_input.requested" not in event_types
    assert "run.waiting_for_user" not in event_types
    assert event_types[-1] == "run.succeeded"


class ConcurrencyProbeProvider(FakeProvider):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.execution_order: list[tuple[str, str]] = []

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.execution_order.append(("start", invocation.kind))
        try:
            await asyncio.sleep(0.02)
            return await super().generate(invocation)
        finally:
            self.execution_order.append(("finish", invocation.kind))
            self.active -= 1


async def test_research_children_execute_concurrently(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    source_id = await _import_source(database)
    probe = ConcurrencyProbeProvider()
    worker.provider = probe

    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始录播客",
            "source_ids": [source_id],
        },
    )
    assert await worker.run_until_idle() == 3
    assert (await service.get_run(created.id)).status == "waiting_for_user"
    assert probe.max_active == 2
    scaffold_start = probe.execution_order.index(("start", "build_interview_scaffold"))
    assert probe.execution_order.index(("finish", "timeline_research")) < scaffold_start
    assert probe.execution_order.index(("finish", "theme_research")) < scaffold_start


class InvalidCitationProvider(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        if invocation.kind == "theme_research":
            await asyncio.sleep(0.05)
            return await super().generate(invocation)
        if invocation.kind == "timeline_research":
            return ProviderResult(
                content={
                    "timeline_events": [
                        {
                            "label": "Invalid citation",
                            "description": "The reference is outside the task scope.",
                            "time_expression": None,
                            "confidence": 0.5,
                            "source_refs": [
                                {
                                    "source_id": "src_not_allowed",
                                    "source_segment_id": "seg_not_allowed",
                                }
                            ],
                        }
                    ],
                    "open_questions": [],
                },
                provider="fake-invalid",
                model="fake-invalid-v1",
            )
        return await super().generate(invocation)


class QuoteMismatchOnceProvider(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        result = await super().generate(invocation)
        if invocation.kind == "theme_research" and invocation.attempt == 1:
            result.content["quotes"][0]["quote"] = "这句原话并不在所引用的素材片段里"
        return result


class QuoteMismatchAlwaysProvider(FakeProvider):
    def __init__(self) -> None:
        self.theme_attempts: list[int] = []

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        result = await super().generate(invocation)
        if invocation.kind == "theme_research":
            self.theme_attempts.append(invocation.attempt)
            result.content["quotes"][0]["quote"] = "这句原话并不在所引用的素材片段里"
        return result


async def test_quote_source_mismatch_gets_one_bounded_repair(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    source_id = await _import_source(database)
    worker.provider = QuoteMismatchOnceProvider()

    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始录播客",
            "source_ids": [source_id],
        },
    )
    assert await worker.run_until_idle() == 4

    completed = await service.get_run(created.id)
    assert completed.status == "waiting_for_user"
    assert completed.model_call_count == 4
    theme_task = next(task for task in completed.tasks if task.kind == "theme_research")
    assert theme_task.status == "succeeded"
    assert theme_task.attempt == 2
    events = await service.list_events(created.id)
    assert sum(event.type == "task.retry_scheduled" for event in events) == 1
    retry = next(event for event in events if event.type == "task.retry_scheduled")
    assert retry.payload["error_code"] == "quote_source_mismatch"


async def test_repeated_quote_source_mismatch_retries_once_then_fails_run(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    source_id = await _import_source(database)
    provider = QuoteMismatchAlwaysProvider()
    worker.provider = provider

    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始录播客",
            "source_ids": [source_id],
        },
    )
    assert await worker.run_until_idle() == 3

    failed = await service.get_run(created.id)
    assert failed.status == "failed"
    assert failed.current_step == "failed"
    assert failed.model_call_count == 3
    assert provider.theme_attempts == [1, 2]
    tasks_by_kind = {task.kind: task for task in failed.tasks}
    assert tasks_by_kind["timeline_research"].status == "succeeded"
    assert tasks_by_kind["theme_research"].status == "failed"
    assert tasks_by_kind["theme_research"].attempt == 2
    assert tasks_by_kind["theme_research"].error_code == "quote_source_mismatch"
    assert tasks_by_kind["research_manager"].status == "failed"
    assert tasks_by_kind["research_manager"].error_code == "child_task_failed"

    events = await service.list_events(created.id)
    assert sum(event.type == "task.retry_scheduled" for event in events) == 1
    assert (
        sum(
            event.type == "task.failed" and event.task_id == tasks_by_kind["theme_research"].id
            for event in events
        )
        == 1
    )
    assert events[-1].type == "run.failed"


async def test_invalid_citation_fails_parent_and_fences_late_sibling(
    runtime: tuple[Database, RunService, Worker],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="epiphany")
    database, service, worker = runtime
    source_id = await _import_source(database)
    worker.provider = InvalidCitationProvider()

    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始录播客",
            "source_ids": [source_id],
        },
    )
    assert await worker.run_until_idle() == 2

    failed = await service.get_run(created.id)
    assert failed.status == "failed"
    assert failed.current_step == "failed"
    assert failed.artifacts == []
    tasks_by_kind = {task.kind: task for task in failed.tasks}
    assert tasks_by_kind["timeline_research"].status == "failed"
    assert tasks_by_kind["timeline_research"].error_code == "invalid_source_reference"
    assert tasks_by_kind["theme_research"].status == "cancelled"
    assert tasks_by_kind["research_manager"].status == "failed"
    assert tasks_by_kind["research_manager"].error_code == "child_task_failed"

    events = await service.list_events(created.id)
    assert events[-1].type == "run.failed"
    assert any(
        event.type == "task.cancelled" and event.payload.get("reason") == "sibling_failed"
        for event in events
    )
    operational_events = [getattr(record, "event", None) for record in caplog.records]
    assert "worker.task.failed" in operational_events
    assert "worker.task.stale_result" in operational_events


class InvalidScaffoldCitationProvider(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        result = await super().generate(invocation)
        if invocation.kind == "build_interview_scaffold":
            result.content["sections"][0]["questions"][0]["source_refs"] = [
                {
                    "source_id": "src_not_in_research_bundle",
                    "source_segment_id": "seg_not_in_research_bundle",
                }
            ]
        return result


async def test_invalid_scaffold_citation_fails_after_research_fan_in(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    source_id = await _import_source(database)
    worker.provider = InvalidScaffoldCitationProvider()

    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始录播客",
            "source_ids": [source_id],
        },
    )
    assert await worker.run_until_idle() == 3

    failed = await service.get_run(created.id)
    assert failed.status == "failed"
    assert failed.current_step == "failed"
    assert failed.output_artifact_id is None
    assert failed.model_call_count == 3
    assert {artifact.kind for artifact in failed.artifacts} == {
        "timeline_research_result",
        "theme_research_result",
        "episode_research_bundle",
    }
    tasks_by_kind = {task.kind: task for task in failed.tasks}
    assert tasks_by_kind["timeline_research"].status == "succeeded"
    assert tasks_by_kind["theme_research"].status == "succeeded"
    assert tasks_by_kind["research_manager"].status == "succeeded"
    assert tasks_by_kind["build_interview_scaffold"].status == "failed"
    assert (
        tasks_by_kind["build_interview_scaffold"].error_code == "invalid_scaffold_source_reference"
    )

    events = await service.list_events(created.id)
    event_types = [event.type for event in events]
    assert event_types.index("workflow.fan_in.completed") < event_types.index(
        "workflow.interview_scaffold.queued"
    )
    assert "workflow.interview_scaffold.completed" not in event_types
    assert event_types[-1] == "run.failed"


class InvocationCountingProvider(FakeProvider):
    def __init__(self) -> None:
        self.invoked_kinds: list[str] = []

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        self.invoked_kinds.append(invocation.kind)
        return await super().generate(invocation)


async def test_model_call_limit_two_rejects_scaffold_before_provider_call(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    source_id = await _import_source(database)
    provider = InvocationCountingProvider()
    worker.provider = provider
    worker.max_model_calls_per_run = 2

    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始录播客",
            "source_ids": [source_id],
        },
    )
    assert await worker.run_until_idle() == 3

    failed = await service.get_run(created.id)
    assert failed.status == "failed"
    assert failed.model_call_count == 2
    assert len(failed.model_calls) == 2
    assert set(provider.invoked_kinds) == {
        "timeline_research",
        "theme_research",
    }
    tasks_by_kind = {task.kind: task for task in failed.tasks}
    assert tasks_by_kind["build_interview_scaffold"].status == "failed"
    assert tasks_by_kind["build_interview_scaffold"].error_code == "model_call_limit_exceeded"
    assert {artifact.kind for artifact in failed.artifacts} == {
        "timeline_research_result",
        "theme_research_result",
        "episode_research_bundle",
    }

    events = await service.list_events(created.id)
    assert sum(event.type == "model.call.started" for event in events) == 2
    assert sum(event.type == "model.call.limit_exceeded" for event in events) == 1
    assert events[-1].type == "run.failed"


async def test_episode_research_api_demo_and_missing_source(tmp_path: Path) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'research-api.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        invalid_payload = await client.post(
            "/runs",
            json={
                "workflow_type": "episode-research",
                "payload": {"source_ids": ["src_missing"]},
            },
        )
        assert invalid_payload.status_code == 422

        missing = await client.post(
            "/runs",
            json={
                "workflow_type": "episode-research",
                "payload": {
                    "topic": "五年后重新开始录播客",
                    "source_ids": ["src_missing"],
                },
            },
        )
        assert missing.status_code == 404

        imported = await client.post(
            "/sources",
            json={
                "title": "API research demo",
                "source_type": "podcast_draft",
                "text": "这是一段用于验证来源引用的合成测试文本。",
            },
        )
        source_id = imported.json()["source"]["id"]
        created = await client.post(
            "/runs",
            headers={"x-request-id": "req_research_demo"},
            json={
                "workflow_type": "episode-research",
                "payload": {
                    "topic": "五年后重新开始录播客",
                    "source_ids": [source_id],
                },
            },
        )
        assert created.status_code == 201
        assert created.headers["x-request-id"] == "req_research_demo"
        run_id = created.json()["id"]

        assert await app.state.worker.run_until_idle() == 3
        completed = await client.get(f"/runs/{run_id}")
        assert completed.json()["workflow_version"] == "v4"
        assert completed.json()["status"] == "waiting_for_user"
        assert completed.json()["current_step"] == "awaiting_interview_response"
        assert len(completed.json()["tasks"]) == 4
        assert len(completed.json()["artifacts"]) == 4
        assert completed.json()["model_call_count"] == 3
        output_artifact_id = completed.json()["output_artifact_id"]
        assert (
            next(
                artifact["kind"]
                for artifact in completed.json()["artifacts"]
                if artifact["id"] == output_artifact_id
            )
            == "build_interview_scaffold_result"
        )
    await app.state.database.close()
