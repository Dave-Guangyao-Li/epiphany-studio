from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pytest

from epiphany.config import Settings
from epiphany.db import Database
from epiphany.main import create_app
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
) -> None:
    database, service, worker = runtime
    source_id = await _import_source(database)

    created = await service.create_run(
        workflow_type="episode-research",
        payload={"source_ids": [source_id]},
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

    assert await worker.run_until_idle() == 2

    completed = await service.get_run(created.id)
    assert completed.status == "succeeded"
    assert completed.current_step == "complete"
    assert completed.model_call_count == 2
    assert len(completed.artifacts) == 3
    assert {artifact.kind for artifact in completed.artifacts} == {
        "timeline_research_result",
        "theme_research_result",
        "episode_research_bundle",
    }
    assert (
        next(task for task in completed.tasks if task.kind == "research_manager").status
        == "succeeded"
    )
    assert completed.output_artifact_id == next(
        artifact.id
        for artifact in completed.artifacts
        if artifact.kind == "episode_research_bundle"
    )

    events = await service.list_events(created.id)
    event_types = [event.type for event in events]
    assert "workflow.fan_out.started" in event_types
    assert "workflow.fan_in.waiting" in event_types
    assert "workflow.fan_in.completed" in event_types
    assert event_types[-1] == "run.succeeded"


class ConcurrencyProbeProvider(FakeProvider):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            return await super().generate(invocation)
        finally:
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
        payload={"source_ids": [source_id]},
    )
    assert await worker.run_until_idle() == 2
    assert (await service.get_run(created.id)).status == "succeeded"
    assert probe.max_active == 2


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
        payload={"source_ids": [source_id]},
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
        missing = await client.post(
            "/runs",
            json={
                "workflow_type": "episode-research",
                "payload": {"source_ids": ["src_missing"]},
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
                "payload": {"source_ids": [source_id]},
            },
        )
        assert created.status_code == 201
        assert created.headers["x-request-id"] == "req_research_demo"
        run_id = created.json()["id"]

        assert await app.state.worker.run_until_idle() == 2
        completed = await client.get(f"/runs/{run_id}")
        assert completed.json()["status"] == "succeeded"
        assert len(completed.json()["artifacts"]) == 3
    await app.state.database.close()
