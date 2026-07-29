from __future__ import annotations

from epiphany.db import Database
from epiphany.editor_schemas import BUILD_PODCAST_DRAFT
from epiphany.runtime.providers import (
    FakeProvider,
    ProviderResult,
    RetryableProviderError,
    TaskInvocation,
)
from epiphany.runtime.worker import Worker
from epiphany.services import RunService
from epiphany.source_service import SourceService


async def _resume_with_supplement(
    database: Database,
    service: RunService,
    worker: Worker,
) -> str:
    initial = await SourceService(database).import_text(
        title="Editor 初始素材",
        source_type="podcast_draft",
        text=(
            "2021 年我录过几段播客。五年后重新打开时，我发现声音留下了当时的紧张和对未来的期待。"
        ),
        metadata={"test": "editor_workflow"},
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新打开播客",
            "source_ids": [initial.source.id],
        },
    )
    assert await worker.run_until_idle() == 3

    supplemental = await SourceService(database).import_text(
        title="Editor 补充口述",
        source_type="voice_note_transcript",
        text="我后来意识到，声音保存的不只是内容，也保存了停顿和呼吸。",
        metadata={"test": "editor_workflow"},
    )
    resumed = await service.resume_run(
        created.id,
        checkpoint="interview_scaffold",
        submission_id="editor-round-1",
        source_ids=[supplemental.source.id],
    )
    assert resumed.run.status == "running"
    return created.id


class FailEditorOnceProvider(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        if invocation.kind == BUILD_PODCAST_DRAFT and invocation.attempt == 1:
            raise RetryableProviderError("temporary Editor failure")
        return await super().generate(invocation)


async def test_editor_retry_is_traced_and_commits_one_final_artifact(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    worker.provider = FailEditorOnceProvider()
    run_id = await _resume_with_supplement(database, service, worker)

    assert await worker.run_until_idle() == 2
    completed = await service.get_run(run_id)

    assert completed.status == "succeeded"
    assert completed.model_call_count == 5
    editor = next(task for task in completed.tasks if task.kind == BUILD_PODCAST_DRAFT)
    assert editor.status == "succeeded"
    assert editor.attempt == 2
    assert (
        len(
            [
                artifact
                for artifact in completed.artifacts
                if artifact.kind == f"{BUILD_PODCAST_DRAFT}_result"
            ]
        )
        == 1
    )
    editor_calls = [call for call in completed.model_calls if call.task_id == editor.id]
    assert [call.status for call in editor_calls] == ["failed", "succeeded"]
    assert [call.attempt for call in editor_calls] == [1, 2]
    events = await service.list_events(run_id)
    assert sum(event.type == "task.retry_scheduled" for event in events) == 1
    assert sum(event.type == "workflow.editor.completed" for event in events) == 1


class CountingFakeProvider(FakeProvider):
    def __init__(self) -> None:
        self.invocations: list[str] = []

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        self.invocations.append(invocation.kind)
        return await super().generate(invocation)


async def test_editor_respects_per_run_model_call_budget_before_provider_invocation(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    provider = CountingFakeProvider()
    worker.provider = provider
    worker.max_model_calls_per_run = 3
    run_id = await _resume_with_supplement(database, service, worker)

    assert await worker.run_until_idle() == 1
    failed = await service.get_run(run_id)

    assert failed.status == "failed"
    assert failed.model_call_count == 3
    assert BUILD_PODCAST_DRAFT not in provider.invocations
    editor = next(task for task in failed.tasks if task.kind == BUILD_PODCAST_DRAFT)
    assert editor.status == "failed"
    assert editor.error_code == "model_call_limit_exceeded"
    assert all(artifact.kind != f"{BUILD_PODCAST_DRAFT}_result" for artifact in failed.artifacts)
    events = await service.list_events(run_id)
    assert "model.call.limit_exceeded" in [event.type for event in events]
    assert "run.failed" in [event.type for event in events]
