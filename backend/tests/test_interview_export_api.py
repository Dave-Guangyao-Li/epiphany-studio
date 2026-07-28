from __future__ import annotations

import logging
from pathlib import Path

import httpx

from epiphany.config import Settings
from epiphany.main import create_app
from epiphany.models import Artifact, Run
from epiphany.state_machine import RunStatus

SOURCE_REF = {
    "source_id": "src_export",
    "source_segment_id": "seg_export",
}


def _scaffold_content() -> dict[str, object]:
    section = {
        "title": "重新听见自己",
        "source_refs": [SOURCE_REF],
        "known_context": [
            {
                "text": "五年后，用户重新打开了以前的播客。",
                "source_refs": [SOURCE_REF],
            }
        ],
        "transition": {
            "text": "先从按下播放键的那个瞬间说起。",
            "source_refs": [SOURCE_REF],
        },
        "questions": [
            {
                "prompt": "第一次听见以前的声音时，你注意到了什么？",
                "purpose": "补充具体的感官细节。",
                "keywords": ["声音", "时间"],
                "source_refs": [SOURCE_REF],
            }
        ],
    }
    return {
        "title": "五年后，我重新打开了这个播客",
        "episode_intent": {
            "text": "理解声音为什么能够成为跨越时间的记录。",
            "source_refs": [SOURCE_REF],
        },
        "opening": {
            "text": "前几天，我重新打开了一个很久没有更新的播客。",
            "source_refs": [SOURCE_REF],
        },
        "sections": [
            section,
            {**section, "title": "声音留下了什么"},
        ],
        "material_gaps": [],
        "closing": {
            "text": "先把问题留在这里，等新的回忆慢慢出现。",
            "source_refs": [SOURCE_REF],
        },
        "_execution": {
            "provider": "fake",
            "model": "fake-v1",
            "attempt": 1,
        },
    }


async def _insert_run(
    app: object,
    *,
    status: RunStatus,
    artifact_kind: str | None = None,
    artifact_content: dict[str, object] | None = None,
) -> str:
    database = app.state.database
    async with database.sessions() as session, session.begin():
        run = Run(
            workflow_type="episode-research",
            workflow_version="v1",
            status=status,
            current_step="complete" if status == RunStatus.SUCCEEDED else "research_fan_out",
            input_json={
                "topic": "五年后重新开始录播客",
                "source_ids": ["src_export"],
            },
        )
        session.add(run)
        await session.flush()
        if artifact_kind is not None:
            artifact = Artifact(
                run_id=run.id,
                kind=artifact_kind,
                content_json=artifact_content or {},
                idempotency_key=f"export-test:{run.id}",
            )
            session.add(artifact)
            await session.flush()
            run.output_artifact_id = artifact.id
        run_id = run.id
    return run_id


async def test_export_interview_scaffold_markdown_api(tmp_path: Path, caplog: object) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'export-api.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    run_id = await _insert_run(
        app,
        status=RunStatus.SUCCEEDED,
        artifact_kind="build_interview_scaffold_result",
        artifact_content=_scaffold_content(),
    )

    caplog.set_level(logging.INFO, logger="epiphany.run_service")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/runs/{run_id}/exports/interview-scaffold.md",
            headers={"x-request-id": "req_export_test"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="interview-scaffold-{run_id}.md"'
    )
    assert response.headers["x-request-id"] == "req_export_test"
    assert response.text.startswith("# 五年后，我重新打开了这个播客")
    assert "`src_export#seg_export`" in response.text
    assert "_execution" not in response.text

    export_record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "run.interview_scaffold_markdown.exported"
    )
    assert export_record.run_id == run_id
    assert export_record.markdown_char_count == len(response.text)
    await app.state.database.close()


async def test_export_returns_404_for_missing_run(tmp_path: Path) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'missing-export.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/runs/run_missing/exports/interview-scaffold.md")

    assert response.status_code == 404
    assert response.json() == {"detail": "run not found"}
    await app.state.database.close()


async def test_export_returns_409_until_scaffold_is_ready(tmp_path: Path) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'not-ready-export.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    queued_run_id = await _insert_run(app, status=RunStatus.QUEUED)
    wrong_output_run_id = await _insert_run(
        app,
        status=RunStatus.SUCCEEDED,
        artifact_kind="research_bundle",
        artifact_content={"timeline": {}, "themes": {}},
    )
    invalid_output_run_id = await _insert_run(
        app,
        status=RunStatus.SUCCEEDED,
        artifact_kind="build_interview_scaffold_result",
        artifact_content={"title": "incomplete"},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        queued = await client.get(f"/runs/{queued_run_id}/exports/interview-scaffold.md")
        wrong_output = await client.get(
            f"/runs/{wrong_output_run_id}/exports/interview-scaffold.md"
        )
        invalid_output = await client.get(
            f"/runs/{invalid_output_run_id}/exports/interview-scaffold.md"
        )

    assert queued.status_code == 409
    assert queued.json() == {"detail": "interview scaffold is not ready for export"}
    assert wrong_output.status_code == 409
    assert wrong_output.json() == {"detail": "run output is not an interview scaffold"}
    assert invalid_output.status_code == 409
    assert invalid_output.json() == {"detail": "interview scaffold output is invalid"}
    await app.state.database.close()
