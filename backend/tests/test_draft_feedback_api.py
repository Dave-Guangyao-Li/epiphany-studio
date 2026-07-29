from __future__ import annotations

import logging
from pathlib import Path

import httpx

from epiphany.config import Settings
from epiphany.main import create_app
from epiphany.models import Artifact, Run
from epiphany.state_machine import RunStatus


async def _insert_run_with_output(
    app: object,
    *,
    status: RunStatus,
    artifact_kind: str = "build_podcast_draft_result",
) -> tuple[str, str | None]:
    database = app.state.database
    async with database.sessions() as session, session.begin():
        run = Run(
            workflow_type="episode-research",
            workflow_version="v5",
            status=status,
            current_step="complete" if status == RunStatus.SUCCEEDED else "research_fan_out",
            input_json={
                "topic": "五年后重新开始录播客",
                "source_ids": ["src_feedback"],
            },
        )
        session.add(run)
        await session.flush()

        artifact_id: str | None = None
        if status == RunStatus.SUCCEEDED:
            artifact = Artifact(
                run_id=run.id,
                kind=artifact_kind,
                content_json={"podcast_script": {"title": "一封跨越五年的语音信"}},
                idempotency_key=f"feedback-draft:{run.id}",
            )
            session.add(artifact)
            await session.flush()
            run.output_artifact_id = artifact.id
            artifact_id = artifact.id

        return run.id, artifact_id


def _feedback_payload(
    *,
    submission_id: str,
    feedback_origin: str = "human",
    comment: str | None = "这份稿子已经比较像我，但第二段还可以更口语一些。",
) -> dict[str, object]:
    return {
        "submission_id": submission_id,
        "feedback_origin": feedback_origin,
        "decision": "accepted",
        "overall_rating": 4,
        "voice_match_rating": 4,
        "recordability_rating": 5,
        "usefulness_rating": 4,
        "tone_fit_rating": 4,
        "would_record_as_is": True,
        "comment": comment,
    }


async def test_feedback_create_replay_conflict_list_and_signal_origin(
    tmp_path: Path,
    caplog: object,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'draft-feedback.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    run_id, draft_artifact_id = await _insert_run_with_output(
        app,
        status=RunStatus.SUCCEEDED,
    )
    private_comment = "只应该保存在反馈 Artifact 中：我不想让这句话进入事件或日志。"
    human_payload = _feedback_payload(
        submission_id="  ep0   review-1  ",
        comment=private_comment,
    )
    synthetic_payload = _feedback_payload(
        submission_id="m3.4-e2e-review",
        feedback_origin="synthetic_test",
        comment="这是自动化测试提交，不是真实用户评价。",
    )

    caplog.set_level(logging.INFO, logger="epiphany")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            f"/runs/{run_id}/quality-feedback",
            headers={"x-request-id": "req_feedback_create"},
            json=human_payload,
        )
        assert created.status_code == 200
        assert created.headers["x-request-id"] == "req_feedback_create"
        created_body = created.json()
        assert created_body["idempotent_replay"] is False
        assert created_body["feedback"] == {
            **human_payload,
            "schema_version": "draft_user_feedback_v1",
            "submission_id": "ep0 review-1",
            "draft_artifact_id": draft_artifact_id,
            "human_signal_eligible": True,
            "observed_duration_minutes": None,
        }
        assert created_body["artifact"]["kind"] == "draft_user_feedback"
        assert created_body["artifact"]["content_json"] == created_body["feedback"]

        events_after_create = await client.get(f"/runs/{run_id}/events")
        assert events_after_create.status_code == 200
        assert [event["type"] for event in events_after_create.json()] == [
            "workflow.draft_quality.feedback_recorded"
        ]
        event_payload = events_after_create.json()[0]["payload"]
        assert event_payload == {
            "feedback_artifact_id": created_body["artifact"]["id"],
            "feedback_origin": "human",
            "feedback_decision": "accepted",
            "human_signal_eligible": True,
            "overall_rating": 4,
            "would_record_as_is": True,
        }
        assert private_comment not in events_after_create.text

        replay = await client.post(
            f"/runs/{run_id}/quality-feedback",
            json=human_payload,
        )
        assert replay.status_code == 200
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["artifact"]["id"] == created_body["artifact"]["id"]

        events_after_replay = await client.get(f"/runs/{run_id}/events")
        assert events_after_replay.json() == events_after_create.json()

        conflicting_payload = {
            **human_payload,
            "overall_rating": 2,
        }
        conflict = await client.post(
            f"/runs/{run_id}/quality-feedback",
            json=conflicting_payload,
        )
        assert conflict.status_code == 409
        assert conflict.json() == {
            "detail": "submission_id was already used with different feedback"
        }

        synthetic = await client.post(
            f"/runs/{run_id}/quality-feedback",
            json=synthetic_payload,
        )
        assert synthetic.status_code == 200
        assert synthetic.json()["feedback"]["feedback_origin"] == "synthetic_test"
        assert synthetic.json()["feedback"]["human_signal_eligible"] is False

        listed = await client.get(f"/runs/{run_id}/quality-feedback")
        assert listed.status_code == 200
        by_submission = {record["feedback"]["submission_id"]: record for record in listed.json()}
        assert set(by_submission) == {"ep0 review-1", "m3.4-e2e-review"}
        assert by_submission["ep0 review-1"]["feedback"]["human_signal_eligible"] is True
        assert by_submission["m3.4-e2e-review"]["feedback"]["human_signal_eligible"] is False
        assert by_submission["ep0 review-1"]["feedback"]["comment"] == private_comment

    feedback_logs = [
        record
        for record in caplog.records
        if getattr(record, "event", "").startswith("workflow.draft_quality.feedback_")
    ]
    assert [record.event for record in feedback_logs] == [
        "workflow.draft_quality.feedback_recorded",
        "workflow.draft_quality.feedback_replayed",
        "workflow.draft_quality.feedback_recorded",
    ]
    assert feedback_logs[0].feedback_origin == "human"
    assert feedback_logs[0].feedback_rating == 4
    assert feedback_logs[0].human_signal_eligible is True
    assert feedback_logs[-1].feedback_origin == "synthetic_test"
    assert feedback_logs[-1].human_signal_eligible is False
    assert private_comment not in caplog.text
    await app.state.database.close()


async def test_feedback_rejects_invalid_run_state_output_and_request_scores(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'draft-feedback-invalid.db'}",
            create_schema_on_start=False,
            worker_enabled=False,
        )
    )
    await app.state.database.create_schema()
    running_run_id, _ = await _insert_run_with_output(app, status=RunStatus.RUNNING)
    wrong_output_run_id, _ = await _insert_run_with_output(
        app,
        status=RunStatus.SUCCEEDED,
        artifact_kind="build_interview_scaffold_result",
    )
    valid_payload = _feedback_payload(submission_id="review-invalid-state")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        running = await client.post(
            f"/runs/{running_run_id}/quality-feedback",
            json=valid_payload,
        )
        assert running.status_code == 409
        assert running.json() == {
            "detail": "draft feedback requires a succeeded Run with a podcast draft"
        }

        wrong_output = await client.post(
            f"/runs/{wrong_output_run_id}/quality-feedback",
            json=valid_payload,
        )
        assert wrong_output.status_code == 409
        assert wrong_output.json() == {
            "detail": "draft feedback requires a succeeded Run with a podcast draft"
        }

        for invalid_rating in (0, 6):
            invalid_payload = {
                **valid_payload,
                "overall_rating": invalid_rating,
            }
            invalid_score = await client.post(
                f"/runs/{wrong_output_run_id}/quality-feedback",
                json=invalid_payload,
            )
            assert invalid_score.status_code == 422

        client_owned_eligibility = await client.post(
            f"/runs/{wrong_output_run_id}/quality-feedback",
            json={
                **valid_payload,
                "human_signal_eligible": True,
            },
        )
        assert client_owned_eligibility.status_code == 422

        missing = await client.get("/runs/run_missing/quality-feedback")
        assert missing.status_code == 404
        assert missing.json() == {"detail": "run not found"}

    await app.state.database.close()
