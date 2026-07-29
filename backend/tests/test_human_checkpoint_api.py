from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pytest

from epiphany.config import Settings
from epiphany.db import Database
from epiphany.main import create_app
from epiphany.models import Run
from epiphany.runtime.worker import Worker
from epiphany.services import (
    RunAlreadyTerminal,
    RunResumeNotAllowed,
    RunService,
)
from epiphany.source_service import SourceService


async def _create_waiting_run(app: object) -> str:
    imported = await app.state.source_service.import_text(
        title="M3.1 初始素材",
        source_type="podcast_draft",
        text="五年前开始录播客，后来重新听见了当时的声音。",
        metadata={"purpose": "human_checkpoint_test"},
    )
    created = await app.state.run_service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始录播客",
            "source_ids": [imported.source.id],
        },
    )
    assert await app.state.worker.run_until_idle() == 3
    waiting = await app.state.run_service.get_run(created.id)
    assert waiting.status == "waiting_for_user"
    return created.id


async def _import_supplemental_source(app: object, *, text: str) -> str:
    imported = await app.state.source_service.import_text(
        title="第一轮口述补充",
        source_type="voice_note_transcript",
        text=text,
        metadata={"round": 1},
    )
    return imported.source.id


async def test_resume_api_accepts_user_source_and_replays_idempotently(
    tmp_path: Path,
    caplog: object,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'resume-api.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    run_id = await _create_waiting_run(app)
    transcript = "这是只应该保存在 Source 里的补充口述，不应出现在 Resume 日志中。"
    source_id = await _import_supplemental_source(app, text=transcript)
    payload = {
        "checkpoint": "interview_scaffold",
        "submission_id": "ep0-round-1",
        "source_ids": [source_id],
    }

    caplog.set_level(logging.INFO, logger="epiphany")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        waiting = await client.get(f"/runs/{run_id}")
        scaffold_artifact_id = waiting.json()["output_artifact_id"]
        assert waiting.json()["status"] == "waiting_for_user"
        assert len(waiting.json()["artifacts"]) == 4
        assert waiting.json()["model_call_count"] == 3

        waiting_export = await client.get(f"/runs/{run_id}/exports/interview-scaffold.md")
        assert waiting_export.status_code == 200

        before_events = await client.get(f"/runs/{run_id}/events")
        before_event_count = len(before_events.json())
        assert before_events.json()[-1]["type"] == "run.waiting_for_user"
        assert all(event["type"] != "run.succeeded" for event in before_events.json())

        resumed = await client.post(
            f"/runs/{run_id}/resume",
            headers={"x-request-id": "req_resume_first"},
            json=payload,
        )
        assert resumed.status_code == 200
        assert resumed.headers["x-request-id"] == "req_resume_first"
        resumed_body = resumed.json()
        assert resumed_body["resumed"] is True
        assert resumed_body["idempotent_replay"] is False
        assert resumed_body["run"]["status"] == "running"
        assert resumed_body["run"]["current_step"] == "build_podcast_draft"
        assert resumed_body["run"]["output_artifact_id"] == scaffold_artifact_id
        assert resumed_body["run"]["model_call_count"] == 3
        assert len(resumed_body["run"]["artifacts"]) == 5
        assert len(resumed_body["run"]["tasks"]) == 5
        editor_task = next(
            task for task in resumed_body["run"]["tasks"] if task["kind"] == "build_podcast_draft"
        )
        assert editor_task["status"] == "queued"

        submission = next(
            artifact
            for artifact in resumed_body["run"]["artifacts"]
            if artifact["id"] == resumed_body["submission_artifact_id"]
        )
        assert submission["kind"] == "user_material_submission"
        assert submission["content_json"]["source_ids"] == [source_id]
        assert submission["content_json"]["source_refs"]
        assert transcript not in str(submission["content_json"])

        resumed_events = await client.get(f"/runs/{run_id}/events")
        resumed_event_types = [event["type"] for event in resumed_events.json()]
        assert resumed_event_types[-4:] == [
            "run.resumed",
            "workflow.user_material.accepted",
            "task.queued",
            "workflow.editor.queued",
        ]

        replay = await client.post(f"/runs/{run_id}/resume", json=payload)
        assert replay.status_code == 200
        replay_body = replay.json()
        assert replay_body["resumed"] is False
        assert replay_body["idempotent_replay"] is True
        assert replay_body["submission_artifact_id"] == resumed_body["submission_artifact_id"]
        assert len(replay_body["run"]["artifacts"]) == 5
        assert replay_body["run"]["model_call_count"] == 3

        replay_events = await client.get(f"/runs/{run_id}/events")
        assert len(replay_events.json()) == before_event_count + 4

        assert await app.state.worker.run_until_idle() == 1
        completed = await client.get(f"/runs/{run_id}")
        completed_body = completed.json()
        assert completed_body["status"] == "succeeded"
        assert completed_body["current_step"] == "complete"
        assert completed_body["output_artifact_id"] != scaffold_artifact_id
        assert completed_body["model_call_count"] == 4
        assert len(completed_body["tasks"]) == 5
        assert len(completed_body["artifacts"]) == 6
        assert (
            next(
                artifact["kind"]
                for artifact in completed_body["artifacts"]
                if artifact["id"] == completed_body["output_artifact_id"]
            )
            == "build_podcast_draft_result"
        )

        completed_export = await client.get(f"/runs/{run_id}/exports/interview-scaffold.md")
        assert completed_export.status_code == 200
        draft_export = await client.get(f"/runs/{run_id}/exports/podcast-draft.md")
        notes_export = await client.get(f"/runs/{run_id}/exports/show-notes.md")
        assert draft_export.status_code == 200
        assert notes_export.status_code == 200
        assert "[S" in draft_export.text
        assert transcript in draft_export.text

    assert transcript not in caplog.text
    assert "run.resume.accepted" in [getattr(record, "event", None) for record in caplog.records]
    assert "run.resume.idempotent_replay" in [
        getattr(record, "event", None) for record in caplog.records
    ]
    await app.state.database.close()


async def test_final_exports_are_404_for_missing_run_and_409_until_editor_finishes(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'final-export-state.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    run_id = await _create_waiting_run(app)
    transport = httpx.ASGITransport(app=app)
    export_paths = ("podcast-draft.md", "show-notes.md")

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for export_path in export_paths:
            missing = await client.get(f"/runs/run_missing/exports/{export_path}")
            not_ready = await client.get(f"/runs/{run_id}/exports/{export_path}")
            assert missing.status_code == 404
            assert not_ready.status_code == 409

        source_id = await _import_supplemental_source(
            app,
            text="Editor 还没有运行时，最终导出仍然应该返回未就绪。",
        )
        resumed = await client.post(
            f"/runs/{run_id}/resume",
            json={
                "checkpoint": "interview_scaffold",
                "submission_id": "export-not-ready",
                "source_ids": [source_id],
            },
        )
        assert resumed.status_code == 200
        assert resumed.json()["run"]["status"] == "running"

        for export_path in export_paths:
            queued = await client.get(f"/runs/{run_id}/exports/{export_path}")
            assert queued.status_code == 409
            assert queued.json() == {"detail": "podcast draft is not ready for export"}

    await app.state.database.close()


async def test_resume_api_rejects_missing_source_conflict_and_wrong_state(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'resume-errors.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    run_id = await _create_waiting_run(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post(
            f"/runs/{run_id}/resume",
            json={
                "checkpoint": "interview_scaffold",
                "submission_id": "missing-source",
                "source_ids": ["src_missing"],
            },
        )
        assert missing.status_code == 404
        still_waiting = await client.get(f"/runs/{run_id}")
        assert still_waiting.json()["status"] == "waiting_for_user"
        assert len(still_waiting.json()["artifacts"]) == 4

        first_source_id = await _import_supplemental_source(
            app,
            text="第一份补充素材。",
        )
        first_payload = {
            "checkpoint": "interview_scaffold",
            "submission_id": "same-submission",
            "source_ids": [first_source_id],
        }
        first = await client.post(f"/runs/{run_id}/resume", json=first_payload)
        assert first.status_code == 200

        second_source_id = await _import_supplemental_source(
            app,
            text="同一个 submission ID 下不同的补充素材。",
        )
        conflict = await client.post(
            f"/runs/{run_id}/resume",
            json={**first_payload, "source_ids": [second_source_id]},
        )
        assert conflict.status_code == 409
        assert "different material" in conflict.json()["detail"]

        wrong_state = await client.post(
            f"/runs/{run_id}/resume",
            json={
                **first_payload,
                "submission_id": "another-submission",
            },
        )
        assert wrong_state.status_code == 409

        unknown_run = await client.post(
            "/runs/run_missing/resume",
            json=first_payload,
        )
        assert unknown_run.status_code == 404

        invalid_payload = await client.post(
            f"/runs/{run_id}/resume",
            json={
                "checkpoint": "interview_scaffold",
                "submission_id": " ",
                "source_ids": [],
            },
        )
        assert invalid_payload.status_code == 422

    await app.state.database.close()


async def test_resume_api_rejects_initial_source_as_supplement_without_mutation(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'resume-overlap.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    run_id = await _create_waiting_run(app)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        before_run = (await client.get(f"/runs/{run_id}")).json()
        before_events = (await client.get(f"/runs/{run_id}/events")).json()
        initial_source_id = before_run["input_json"]["source_ids"][0]

        rejected = await client.post(
            f"/runs/{run_id}/resume",
            json={
                "checkpoint": "interview_scaffold",
                "submission_id": "initial-source-is-not-a-supplement",
                "source_ids": [initial_source_id],
            },
        )

        assert rejected.status_code == 409
        assert rejected.json() == {"detail": "submitted material cannot build a valid Editor task"}
        after_run = (await client.get(f"/runs/{run_id}")).json()
        after_events = (await client.get(f"/runs/{run_id}/events")).json()
        assert after_run["status"] == "waiting_for_user"
        assert after_run["current_step"] == "awaiting_interview_response"
        assert [item["id"] for item in after_run["artifacts"]] == [
            item["id"] for item in before_run["artifacts"]
        ]
        assert [item["id"] for item in after_run["tasks"]] == [
            item["id"] for item in before_run["tasks"]
        ]
        assert [item["id"] for item in after_run["model_calls"]] == [
            item["id"] for item in before_run["model_calls"]
        ]
        assert [item["id"] for item in after_events] == [item["id"] for item in before_events]

    await app.state.database.close()


async def test_resume_service_rejects_more_than_500_supplemental_segments_atomically(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    initial = await SourceService(database).import_text(
        title="Editor 输入上限初始素材",
        source_type="podcast_draft",
        text="这段初始素材用于生成等待补充口述的采访脚手架。",
        metadata={"test": "editor_input_limit"},
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "验证 Editor 输入上限",
            "source_ids": [initial.source.id],
        },
    )
    assert await worker.run_until_idle() == 3
    oversized = await SourceService(database).import_text(
        title="超过 Editor 上限的补充素材",
        source_type="voice_note_transcript",
        text="\n\n".join(f"第 {index} 段补充口述。" for index in range(501)),
        metadata={"test": "editor_input_limit"},
    )
    assert oversized.source.segment_count == 501
    before_run = await service.get_run(created.id)
    before_events = await service.list_events(created.id)

    with pytest.raises(
        RunResumeNotAllowed,
        match="submitted material cannot build a valid Editor task",
    ):
        await service.resume_run(
            created.id,
            checkpoint="interview_scaffold",
            submission_id="too-many-supplemental-segments",
            source_ids=[oversized.source.id],
        )

    after_run = await service.get_run(created.id)
    after_events = await service.list_events(created.id)
    assert after_run.status == "waiting_for_user"
    assert after_run.current_step == "awaiting_interview_response"
    assert [item.id for item in after_run.artifacts] == [item.id for item in before_run.artifacts]
    assert [item.id for item in after_run.tasks] == [item.id for item in before_run.tasks]
    assert [item.id for item in after_run.model_calls] == [
        item.id for item in before_run.model_calls
    ]
    assert [item.id for item in after_events] == [item.id for item in before_events]


async def test_waiting_checkpoint_survives_restart_before_resume(tmp_path: Path) -> None:
    database_path = tmp_path / "resume-restart.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{database_path}",
        create_schema_on_start=False,
        worker_enabled=False,
    )
    first_app = create_app(settings=settings)
    await first_app.state.database.create_schema()
    run_id = await _create_waiting_run(first_app)
    await first_app.state.database.close()

    restarted_app = create_app(settings=settings)
    waiting = await restarted_app.state.run_service.get_run(run_id)
    assert waiting.status == "waiting_for_user"
    assert waiting.current_step == "awaiting_interview_response"
    assert await restarted_app.state.worker.run_until_idle() == 0

    source_id = await _import_supplemental_source(
        restarted_app,
        text="服务重启以后才提交的补充口述。",
    )
    result = await restarted_app.state.run_service.resume_run(
        run_id,
        checkpoint="interview_scaffold",
        submission_id="after-restart",
        source_ids=[source_id],
    )
    assert result.resumed is True
    assert result.run.status == "running"
    assert result.run.model_call_count == 3
    await restarted_app.state.database.close()

    editor_app = create_app(settings=settings)
    assert await editor_app.state.worker.run_until_idle() == 1
    completed = await editor_app.state.run_service.get_run(run_id)
    assert completed.status == "succeeded"
    assert completed.model_call_count == 4
    assert len(completed.artifacts) == 6
    await editor_app.state.database.close()


async def test_persisted_v3_checkpoint_keeps_pre_editor_resume_semantics(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    initial = await SourceService(database).import_text(
        title="v3 兼容初始素材",
        source_type="podcast_draft",
        text="这条 Run 模拟 M3.1 部署期间已经停在人工检查点的数据。",
        metadata={},
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={"topic": "v3 兼容", "source_ids": [initial.source.id]},
    )
    assert await worker.run_until_idle() == 3
    async with database.sessions() as session, session.begin():
        run = await session.get(Run, created.id)
        assert run is not None
        run.workflow_version = "v3"

    supplemental = await SourceService(database).import_text(
        title="v3 兼容补充素材",
        source_type="voice_note_transcript",
        text="旧版本 Resume 不应因为部署 M3.2 而产生新的付费调用。",
        metadata={},
    )
    resumed = await service.resume_run(
        created.id,
        checkpoint="interview_scaffold",
        submission_id="persisted-v3",
        source_ids=[supplemental.source.id],
    )

    assert resumed.run.workflow_version == "v3"
    assert resumed.run.status == "succeeded"
    assert resumed.run.model_call_count == 3
    assert all(task.kind != "build_podcast_draft" for task in resumed.run.tasks)
    assert await worker.run_until_idle() == 0


async def test_concurrent_identical_resume_is_applied_once(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    initial = await SourceService(database).import_text(
        title="并发 Resume 初始素材",
        source_type="podcast_draft",
        text="这段素材用于验证并发 Resume。",
        metadata={},
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "并发 Resume",
            "source_ids": [initial.source.id],
        },
    )
    assert await worker.run_until_idle() == 3
    supplemental = await SourceService(database).import_text(
        title="并发补充素材",
        source_type="voice_note_transcript",
        text="两个同时提交的请求应该只保存一次。",
        metadata={},
    )

    results = await asyncio.gather(
        service.resume_run(
            created.id,
            checkpoint="interview_scaffold",
            submission_id="concurrent-submission",
            source_ids=[supplemental.source.id],
        ),
        service.resume_run(
            created.id,
            checkpoint="interview_scaffold",
            submission_id="concurrent-submission",
            source_ids=[supplemental.source.id],
        ),
    )

    assert sorted(result.resumed for result in results) == [False, True]
    assert sorted(result.idempotent_replay for result in results) == [False, True]
    assert len({result.submission_artifact_id for result in results}) == 1
    completed = await service.get_run(created.id)
    assert (
        len(
            [
                artifact
                for artifact in completed.artifacts
                if artifact.kind == "user_material_submission"
            ]
        )
        == 1
    )
    events = await service.list_events(created.id)
    assert sum(event.type == "run.resumed" for event in events) == 1
    assert sum(event.type == "workflow.user_material.accepted" for event in events) == 1
    assert sum(event.type == "workflow.editor.queued" for event in events) == 1
    assert len([task for task in completed.tasks if task.kind == "build_podcast_draft"]) == 1


async def test_waiting_run_can_be_cancelled_and_cannot_resume(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    initial = await SourceService(database).import_text(
        title="取消 checkpoint 初始素材",
        source_type="podcast_draft",
        text="这段素材用于验证等待用户时仍然可以取消。",
        metadata={},
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "取消等待中的 Run",
            "source_ids": [initial.source.id],
        },
    )
    assert await worker.run_until_idle() == 3
    waiting = await service.get_run(created.id)
    assert waiting.status == "waiting_for_user"

    cancelled = await service.cancel_run(created.id)
    assert cancelled.status == "cancelled"

    supplemental = await SourceService(database).import_text(
        title="取消后的补充素材",
        source_type="voice_note_transcript",
        text="已经取消的 Run 不应接受这段补充。",
        metadata={},
    )
    with pytest.raises(RunResumeNotAllowed):
        await service.resume_run(
            created.id,
            checkpoint="interview_scaffold",
            submission_id="after-cancel",
            source_ids=[supplemental.source.id],
        )

    events = await service.list_events(created.id)
    assert events[-1].type == "run.cancelled"
    assert all(event.type != "run.resumed" for event in events)


async def test_concurrent_resume_and_cancel_leave_a_single_cancelled_terminal_state(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    initial = await SourceService(database).import_text(
        title="并发终态初始素材",
        source_type="podcast_draft",
        text="这段素材用于验证恢复和取消不能同时成功。",
        metadata={},
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "恢复与取消竞态",
            "source_ids": [initial.source.id],
        },
    )
    assert await worker.run_until_idle() == 3
    supplemental = await SourceService(database).import_text(
        title="并发终态补充素材",
        source_type="voice_note_transcript",
        text="同一时刻只能由恢复或取消其中一个动作赢得状态转换。",
        metadata={},
    )

    results = await asyncio.gather(
        service.resume_run(
            created.id,
            checkpoint="interview_scaffold",
            submission_id="resume-cancel-race",
            source_ids=[supplemental.source.id],
        ),
        service.cancel_run(created.id),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) in {1, 2}
    assert all(
        not isinstance(result, Exception)
        or isinstance(result, RunAlreadyTerminal | RunResumeNotAllowed)
        for result in results
    )

    final_run = await service.get_run(created.id)
    assert final_run.status == "cancelled"
    assert final_run.model_call_count == 3
    editor_tasks = [task for task in final_run.tasks if task.kind == "build_podcast_draft"]
    assert not editor_tasks or editor_tasks[0].status == "cancelled"
    events = await service.list_events(created.id)
    terminal_event_types = [
        event.type for event in events if event.type in {"run.succeeded", "run.cancelled"}
    ]
    assert terminal_event_types == ["run.cancelled"]
