from __future__ import annotations

import pytest

from epiphany.db import Database
from epiphany.interview_markdown import interview_scaffold_reference_keys
from epiphany.models import Task
from epiphany.runtime.worker import Worker
from epiphany.services import RunResumeConflict, RunResumeNotAllowed, RunService
from epiphany.source_service import SourceService


async def _import_material(
    database: Database,
    *,
    title: str,
    text: str,
) -> str:
    imported = await SourceService(database).import_text(
        title=title,
        source_type="voice_note_transcript",
        text=text,
        metadata={
            "synthetic": True,
            "contains_personal_data": False,
            "test": "quality_contract_workflow",
        },
    )
    return imported.source.id


async def _run_state_ids(service: RunService, run_id: str) -> dict[str, object]:
    run = await service.get_run(run_id)
    events = await service.list_events(run_id)
    return {
        "status": run.status,
        "current_step": run.current_step,
        "output_artifact_id": run.output_artifact_id,
        "tasks": [item.id for item in run.tasks],
        "artifacts": [item.id for item in run.artifacts],
        "model_calls": [item.id for item in run.model_calls],
        "events": [item.id for item in events],
    }


async def test_v5_keeps_waiting_across_insufficient_rounds_then_queues_one_editor(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    initial_a = await _import_material(
        database,
        title="合成初始素材 A",
        text="\n\n".join(f"甲{index}" * 30 for index in range(10)),
    )
    initial_b = await _import_material(
        database,
        title="合成初始素材 B",
        text="乙" * 500,
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "为什么重新开始记录生活",
            "source_ids": [initial_a, initial_b],
            "creative_brief": {
                "target_duration_minutes": 10,
                "speaking_rate_chars_per_minute": 280,
                "scenario": "reflective_solo",
                "target_audience": "正在经历转折的普通听众",
                "communication_goal": "用具体经历解释重新记录的原因",
                "tone": ["真诚", "克制", "自然口语"],
                "must_include": ["重新开始"],
                "avoid_patterns": ["空泛排比", "强行金句"],
            },
            "draft_quality": {"enabled": False},
        },
    )

    assert created.workflow_version == "v5"
    assert await worker.run_until_idle() == 3
    initial_wait = await service.get_run(created.id)
    assert initial_wait.status == "waiting_for_user"
    assert initial_wait.current_step == "awaiting_more_material"
    assert len(initial_wait.tasks) == 4
    assert len(initial_wait.model_calls) == 3
    assert all(task.kind != "build_podcast_draft" for task in initial_wait.tasks)
    initial_report = [
        artifact
        for artifact in initial_wait.artifacts
        if artifact.kind == "material_readiness_report"
    ][0]
    assert initial_report.content_json["status"] == "needs_more_material"
    assert initial_report.content_json["counts"]["supplemental_char_count"] == 0

    too_short = await _import_material(
        database,
        title="合成短补充",
        text="丙" * 100,
    )
    first_resume = await service.resume_run(
        created.id,
        checkpoint="material_readiness",
        submission_id="short-round",
        source_ids=[too_short],
    )
    assert first_resume.resumed is True
    assert first_resume.run.status == "waiting_for_user"
    assert first_resume.run.current_step == "awaiting_more_material"
    assert len(first_resume.run.tasks) == 4
    assert len(first_resume.run.model_calls) == 3
    assert all(task.kind != "build_podcast_draft" for task in first_resume.run.tasks)
    reports_after_short_round = [
        artifact
        for artifact in first_resume.run.artifacts
        if artifact.kind == "material_readiness_report"
    ]
    assert len(reports_after_short_round) == 2
    assert reports_after_short_round[-1].content_json["status"] == "needs_more_material"
    assert reports_after_short_round[-1].content_json["counts"]["supplemental_char_count"] == 100

    replay = await service.resume_run(
        created.id,
        checkpoint="material_readiness",
        submission_id="short-round",
        source_ids=[too_short],
    )
    assert replay.resumed is False
    assert replay.idempotent_replay is True
    assert replay.submission_artifact_id == first_resume.submission_artifact_id
    assert len(replay.run.artifacts) == len(first_resume.run.artifacts)

    enough_text = "\n\n".join(
        f"第{paragraph_index}段补充："
        + "".join(
            f"场景{paragraph_index}-{sentence_index}里的动作、感受和现场细节。"
            for sentence_index in range(80)
        )
        for paragraph_index in range(4)
    )
    enough = await _import_material(
        database,
        title="合成长补充",
        text=enough_text,
    )
    second_resume = await service.resume_run(
        created.id,
        checkpoint="material_readiness",
        submission_id="enough-round",
        source_ids=[enough],
    )
    assert second_resume.run.status == "running"
    assert second_resume.run.current_step == "build_podcast_draft"
    editors = [task for task in second_resume.run.tasks if task.kind == "build_podcast_draft"]
    assert len(editors) == 1
    assert editors[0].status == "queued"

    assert await worker.run_until_idle() == 1
    completed = await service.get_run(created.id)
    assert completed.status == "succeeded"
    assert len(completed.tasks) == 5
    assert len(completed.model_calls) == 4
    final_reports = [
        artifact for artifact in completed.artifacts if artifact.kind == "material_readiness_report"
    ]
    assert len(final_reports) == 3
    assert final_reports[-1].content_json["status"] == "ready"
    expected_supplemental_chars = 100 + sum(not character.isspace() for character in enough_text)
    assert (
        final_reports[-1].content_json["counts"]["supplemental_char_count"]
        == expected_supplemental_chars
    )
    assert final_reports[-1].content_json["counts"]["duplicate_segment_count"] == 0

    async with database.sessions() as session:
        persisted_editor = await session.get(Task, editors[0].id)
        assert persisted_editor is not None
        assert len(persisted_editor.input_json["submission_artifact_ids"]) == 2
        assert persisted_editor.input_json["creative_brief"]["target_duration_minutes"] == 10
        assert {
            segment["source_id"]
            for segment in persisted_editor.input_json["supplemental_source_segments"]
        } == {too_short, enough}
        scaffold = next(
            artifact
            for artifact in completed.artifacts
            if artifact.kind == "build_interview_scaffold_result"
        )
        scaffold_keys = set(
            interview_scaffold_reference_keys(
                {key: value for key, value in scaffold.content_json.items() if key != "_execution"}
            )
        )
        editor_initial_keys = {
            (segment["source_id"], segment["source_segment_id"])
            for segment in persisted_editor.input_json["initial_source_segments"]
        }
        assert editor_initial_keys == scaffold_keys
        initial_segment_count = (
            await SourceService(database).get_source(initial_a)
        ).segment_count + (await SourceService(database).get_source(initial_b)).segment_count
        assert len(editor_initial_keys) < initial_segment_count


async def test_v5_rejects_reused_sources_without_mutating_the_run(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    initial = await _import_material(
        database,
        title="重复来源测试初始素材",
        text="甲" * 300,
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "验证重复素材不会形成伪进展",
            "source_ids": [initial],
            "creative_brief": {"target_duration_minutes": 10},
        },
    )
    assert await worker.run_until_idle() == 3

    before_initial_reuse = await _run_state_ids(service, created.id)
    with pytest.raises(RunResumeNotAllowed, match="not already used"):
        await service.resume_run(
            created.id,
            checkpoint="material_readiness",
            submission_id="reuse-initial",
            source_ids=[initial],
        )
    assert await _run_state_ids(service, created.id) == before_initial_reuse

    first_supplement = await _import_material(
        database,
        title="第一份短补充",
        text="乙" * 100,
    )
    accepted = await service.resume_run(
        created.id,
        checkpoint="material_readiness",
        submission_id="first-short-round",
        source_ids=[first_supplement],
    )
    assert accepted.run.status == "waiting_for_user"

    unused_supplement = await _import_material(
        database,
        title="尚未使用的新补充",
        text="丙" * 100,
    )
    before_prior_reuse = await _run_state_ids(service, created.id)
    with pytest.raises(RunResumeConflict, match="different material"):
        await service.resume_run(
            created.id,
            checkpoint="material_readiness",
            submission_id="first-short-round",
            source_ids=[unused_supplement],
        )
    assert await _run_state_ids(service, created.id) == before_prior_reuse

    for submission_id, source_ids in [
        ("reuse-prior", [first_supplement]),
        ("mix-prior-and-new", [first_supplement, unused_supplement]),
    ]:
        with pytest.raises(RunResumeNotAllowed, match="not already used"):
            await service.resume_run(
                created.id,
                checkpoint="material_readiness",
                submission_id=submission_id,
                source_ids=source_ids,
            )
        assert await _run_state_ids(service, created.id) == before_prior_reuse


async def test_v5_enforces_cumulative_500_segment_limit_atomically(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    initial = await _import_material(
        database,
        title="累计分段上限初始素材",
        text="甲" * 300,
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "验证累计补充素材边界",
            "source_ids": [initial],
            "creative_brief": {"target_duration_minutes": 30},
        },
    )
    assert await worker.run_until_idle() == 3

    first_499 = await _import_material(
        database,
        title="前四百九十九个小片段",
        text="\n\n".join(f"片段{index}" for index in range(499)),
    )
    first = await service.resume_run(
        created.id,
        checkpoint="material_readiness",
        submission_id="segments-1-to-499",
        source_ids=[first_499],
    )
    assert first.run.status == "waiting_for_user"

    boundary_500 = await _import_material(
        database,
        title="第五百个小片段",
        text="第五百个边界片段",
    )
    boundary = await service.resume_run(
        created.id,
        checkpoint="material_readiness",
        submission_id="segment-500",
        source_ids=[boundary_500],
    )
    assert boundary.run.status == "waiting_for_user"

    segment_501 = await _import_material(
        database,
        title="第五百零一个小片段",
        text="第五百零一个越界片段",
    )
    before_overflow = await _run_state_ids(service, created.id)
    with pytest.raises(RunResumeNotAllowed, match="500 segment"):
        await service.resume_run(
            created.id,
            checkpoint="material_readiness",
            submission_id="segment-501",
            source_ids=[segment_501],
        )
    assert await _run_state_ids(service, created.id) == before_overflow
