from __future__ import annotations

import json
import math
from collections import Counter
from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from epiphany.db import Database
from epiphany.draft_feedback_schemas import DraftUserFeedbackRequest
from epiphany.draft_quality_schemas import (
    PERSONAL_STYLE_DIMENSION,
    STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION,
)
from epiphany.editor_schemas import (
    BUILD_PODCAST_DRAFT,
    PodcastDraftOutput,
    editor_output_reference_keys,
)
from epiphany.models import ModelCall, Run, Task
from epiphany.revision_schemas import (
    REVISE_PODCAST_DRAFT,
    CreateDraftRevisionRequest,
    PodcastRevisionTaskInput,
)
from epiphany.runtime.orchestrator import GUIDED_REVISION_WORKFLOW_VERSION
from epiphany.runtime.worker import Worker
from epiphany.services import (
    DraftRevisionConflict,
    DraftRevisionNotAllowed,
    RunService,
)
from epiphany.source_service import SourceService


def _factual_material(prefix: str, *, paragraph_count: int, detail_count: int) -> str:
    return "\n\n".join(
        (
            f"{prefix}第{paragraph_index}段。"
            + "".join(
                (
                    f"那天我先注意到细节{paragraph_index}-{detail_index}，"
                    "接着记下一个动作、一句没说完的话和当时身体里的反应。"
                )
                for detail_index in range(detail_count)
            )
            + "现在回头看，我仍然能区分当时的事实和后来才形成的解释。"
        )
        for paragraph_index in range(paragraph_count)
    )


def _writing_sample() -> str:
    marker = "风格样本里的蓝色旧雨伞只用来验证个人表达通道"
    return "\n\n".join(
        (
            f"{marker}，这是第{index}次提到那天下午。"
            "我写东西的时候不太喜欢急着下结论，总想先把门口的水迹、"
            "杯子变凉的速度和一句话说到一半的停顿留下来。"
            "有些变化当时并没有名字，过一阵子再看，才发现它已经悄悄发生了。"
            "如果非要总结，我宁愿把结论放轻一点，也不想把生活说得太整齐。"
        )
        for index in range(1, 7)
    )


async def _import_source(
    database: Database,
    *,
    title: str,
    source_type: str,
    text: str,
) -> str:
    imported = await SourceService(database).import_text(
        title=title,
        source_type=source_type,
        text=text,
        metadata={
            "synthetic": True,
            "contains_personal_data": False,
            "test": "revision_workflow",
        },
    )
    return imported.source.id


def _draft_reference_keys(content_json: dict[str, object]) -> set[tuple[str, str]]:
    content = {key: value for key, value in content_json.items() if key != "_execution"}
    return set(editor_output_reference_keys(content))


def _spoken_units(
    content_json: dict[str, object],
) -> list[tuple[str, set[tuple[str, str]]]]:
    content = {key: value for key, value in content_json.items() if key != "_execution"}
    draft = PodcastDraftOutput.model_validate(content)
    grounded_units = [
        draft.podcast_script.opening,
        *[
            paragraph
            for section in draft.podcast_script.sections
            for paragraph in section.paragraphs
        ],
        draft.podcast_script.closing,
    ]
    return [
        (
            "".join(unit.text.split()),
            {(reference.source_id, reference.source_segment_id) for reference in unit.source_refs},
        )
        for unit in grounded_units
    ]


def _spoken_character_count(content_json: dict[str, object]) -> int:
    return sum(len(text) for text, _references in _spoken_units(content_json))


def _spoken_reference_keys(content_json: dict[str, object]) -> set[tuple[str, str]]:
    return {
        reference for _text, references in _spoken_units(content_json) for reference in references
    }


async def _create_completed_parent_with_style(
    database: Database,
    service: RunService,
    worker: Worker,
    *,
    supplemental_paragraph_count: int = 10,
) -> tuple[str, str, str]:
    initial_source_id = await _import_source(
        database,
        title="M3.6 初始生活记录",
        source_type="journal",
        text=_factual_material("初始事实", paragraph_count=5, detail_count=7),
    )
    style_source_id = await _import_source(
        database,
        title="M3.6 用户自选写作样本",
        source_type="writing_sample",
        text=_writing_sample(),
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始记录生活",
            "source_ids": [initial_source_id],
            "creative_brief": {
                "target_duration_minutes": 15,
                "speaking_rate_chars_per_minute": 280,
                "scenario": "reflective_solo",
                "target_audience": "正在经历人生转折、想重新开始记录的普通听众",
                "communication_goal": "用具体经历解释为什么重新开始记录",
                "tone": ["真诚", "克制", "自然口语"],
                "must_include": ["重新开始"],
                "avoid_patterns": ["空泛排比", "强行金句"],
            },
            "writing_style_reference": {
                "samples": [
                    {
                        "source_id": style_source_id,
                        "sample_kind": "written_prose",
                    }
                ],
                "ownership_attested": True,
                "model_processing_consent": True,
                "usage": "style_only",
            },
        },
    )
    assert created.workflow_version == GUIDED_REVISION_WORKFLOW_VERSION

    assert await worker.run_until_idle() == 3
    waiting = await service.get_run(created.id)
    assert waiting.status == "waiting_for_user"
    assert waiting.current_step == "awaiting_more_material"

    async with database.sessions() as session:
        research_tasks = (
            (
                await session.execute(
                    select(Task).where(
                        Task.run_id == created.id,
                        Task.kind.in_(["timeline_research", "theme_research"]),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(research_tasks) == 2
    assert all(
        style_source_id not in json.dumps(task.input_json, ensure_ascii=False)
        for task in research_tasks
    )

    supplemental_source_id = await _import_source(
        database,
        title="M3.6 补充口述",
        source_type="voice_note_transcript",
        text=_factual_material(
            "补充口述",
            paragraph_count=supplemental_paragraph_count,
            detail_count=10,
        ),
    )
    resumed = await service.resume_run(
        created.id,
        checkpoint="material_readiness",
        submission_id="m3.6-material-round-1",
        source_ids=[supplemental_source_id],
    )
    assert resumed.resumed is True
    assert resumed.run.status == "running"
    assert resumed.run.current_step == BUILD_PODCAST_DRAFT

    assert await worker.run_until_idle() == 2
    completed = await service.get_run(created.id)
    assert completed.status == "succeeded"
    assert completed.model_call_count == 5
    return created.id, style_source_id, supplemental_source_id


async def test_reuse_unused_material_revision_receives_exact_length_recovery_plan(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    (
        parent_run_id,
        _style_source_id,
        _supplemental_source_id,
    ) = await _create_completed_parent_with_style(
        database,
        service,
        worker,
        supplemental_paragraph_count=15,
    )
    plan_record = await service.get_draft_improvement_plan(parent_run_id)
    plan = plan_record.plan
    assert plan.duration_resolution == "reuse_unused_material", (
        plan.duration.model_dump(),
        plan.material.model_dump(exclude={"unused_source_refs"}),
    )

    created = await service.create_draft_revision(
        parent_run_id,
        request=CreateDraftRevisionRequest(
            submission_id="m3.8-reuse-existing-material",
            selected_actions=["reuse_unused_material"],
            revision_instruction="只展开已有的具体事实，不重复、不虚构，也不要求用完全部素材。",
        ),
    )
    async with database.sessions() as session:
        revision_task = (
            await session.execute(
                select(Task).where(
                    Task.run_id == created.run.id,
                    Task.kind == REVISE_PODCAST_DRAFT,
                )
            )
        ).scalar_one()

    recovery = revision_task.input_json["length_recovery_plan"]
    duration = plan.duration
    minimum_characters = math.ceil(duration.target_script_character_count * 0.85)
    maximum_characters = math.floor(duration.target_script_character_count * 1.15)
    assert recovery["actual_script_character_count"] == (duration.actual_script_character_count)
    assert recovery["minimum_script_character_count"] == minimum_characters
    assert recovery["target_script_character_count"] == (duration.target_script_character_count)
    assert recovery["maximum_script_character_count"] == maximum_characters
    assert recovery["missing_to_minimum_character_count"] == max(
        0,
        minimum_characters - duration.actual_script_character_count,
    )
    assert recovery["missing_to_target_character_count"] == (
        duration.missing_script_character_count
    )

    priority_refs = {
        (reference["source_id"], reference["source_segment_id"])
        for reference in recovery["priority_unused_source_refs"]
    }
    unused_refs = {
        (reference.source_id, reference.source_segment_id)
        for reference in plan.material.unused_source_refs
    }
    factual_refs = {
        (segment["source_id"], segment["source_segment_id"])
        for segment in [
            *revision_task.input_json["initial_source_segments"],
            *revision_task.input_json["supplemental_source_segments"],
        ]
    }
    assert priority_refs
    assert priority_refs <= unused_refs
    assert priority_refs <= factual_refs

    without_recovery = deepcopy(revision_task.input_json)
    without_recovery.pop("length_recovery_plan")
    legacy_task = PodcastRevisionTaskInput.model_validate(without_recovery)
    assert legacy_task.length_recovery_plan is None

    recovery_without_action = deepcopy(revision_task.input_json)
    recovery_without_action["selected_actions"] = ["add_supplemental_material"]
    with pytest.raises(
        ValidationError,
        match="a length_recovery_plan requires reuse_unused_material",
    ):
        PodcastRevisionTaskInput.model_validate(recovery_without_action)


async def test_reuse_unused_material_revision_expands_spoken_script_with_new_evidence(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    (
        parent_run_id,
        _style_source_id,
        _supplemental_source_id,
    ) = await _create_completed_parent_with_style(
        database,
        service,
        worker,
        supplemental_paragraph_count=15,
    )
    parent = await service.get_run(parent_run_id)
    parent_draft = next(
        artifact for artifact in parent.artifacts if artifact.id == parent.output_artifact_id
    )
    plan = (await service.get_draft_improvement_plan(parent_run_id)).plan
    assert plan.duration_resolution == "reuse_unused_material", (
        plan.duration.model_dump(),
        plan.material.model_dump(exclude={"unused_source_refs"}),
    )

    created = await service.create_draft_revision(
        parent_run_id,
        request=CreateDraftRevisionRequest(
            submission_id="m3.8-length-recovery-result",
            selected_actions=["reuse_unused_material"],
            revision_instruction="优先展开未充分使用的具体场景，禁止重复段落和抽象凑字。",
        ),
    )
    worker.max_model_calls_per_run = 2
    assert await worker.run_until_idle() == 2
    child = await service.get_run(created.run.id)
    assert child.status == "succeeded"
    child_draft = next(
        artifact for artifact in child.artifacts if artifact.id == child.output_artifact_id
    )

    parent_characters = _spoken_character_count(parent_draft.content_json)
    child_characters = _spoken_character_count(child_draft.content_json)
    missing_to_minimum = max(
        0,
        math.ceil(plan.duration.target_script_character_count * 0.85) - parent_characters,
    )
    significant_gain = min(
        missing_to_minimum,
        max(280, math.ceil(parent_characters * 0.25)),
    )
    assert child_characters >= parent_characters + significant_gain

    parent_refs = _spoken_reference_keys(parent_draft.content_json)
    child_refs = _spoken_reference_keys(child_draft.content_json)
    assert child_refs - parent_refs
    async with database.sessions() as session:
        revision_task = (
            await session.execute(
                select(Task).where(
                    Task.run_id == child.id,
                    Task.kind == REVISE_PODCAST_DRAFT,
                )
            )
        ).scalar_one()
    allowed_factual_refs = {
        (segment["source_id"], segment["source_segment_id"])
        for segment in [
            *revision_task.input_json["initial_source_segments"],
            *revision_task.input_json["supplemental_source_segments"],
        ]
    }
    assert child_refs <= allowed_factual_refs

    parent_spoken_texts = [text for text, _references in _spoken_units(parent_draft.content_json)]
    child_spoken_texts = [text for text, _references in _spoken_units(child_draft.content_json)]
    parent_exact_repeat_count = sum(
        count - 1 for count in Counter(parent_spoken_texts).values() if count > 1
    )
    child_exact_repeat_count = sum(
        count - 1 for count in Counter(child_spoken_texts).values() if count > 1
    )
    assert child_exact_repeat_count <= parent_exact_repeat_count


async def test_reuse_unused_material_is_rejected_when_plan_does_not_offer_it(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    (
        parent_run_id,
        _style_source_id,
        _supplemental_source_id,
    ) = await _create_completed_parent_with_style(
        database,
        service,
        worker,
        supplemental_paragraph_count=15,
    )
    parent_plan = (await service.get_draft_improvement_plan(parent_run_id)).plan
    assert parent_plan.duration_resolution == "reuse_unused_material"

    recovered = await service.create_draft_revision(
        parent_run_id,
        request=CreateDraftRevisionRequest(
            submission_id="m3.8-create-recovered-parent",
            selected_actions=["reuse_unused_material"],
            revision_instruction="只展开已有的具体事实，不重复、不虚构。",
        ),
    )
    worker.max_model_calls_per_run = 2
    assert await worker.run_until_idle() == 2
    recovered_run = await service.get_run(recovered.run.id)
    assert recovered_run.status == "succeeded"

    recovered_plan = (await service.get_draft_improvement_plan(recovered_run.id)).plan
    assert recovered_plan.duration_resolution == "not_needed"
    assert "reuse_unused_material" not in {option.kind for option in recovered_plan.options}

    async with database.sessions() as session:
        before_counts = {
            "runs": len((await session.execute(select(Run.id))).scalars().all()),
            "tasks": len((await session.execute(select(Task.id))).scalars().all()),
            "model_calls": len((await session.execute(select(ModelCall.id))).scalars().all()),
        }

    with pytest.raises(
        DraftRevisionNotAllowed,
        match="improvement plan does not offer unused factual material",
    ):
        await service.create_draft_revision(
            recovered_run.id,
            request=CreateDraftRevisionRequest(
                submission_id="m3.8-reject-unavailable-reuse",
                selected_actions=["reuse_unused_material"],
                revision_instruction="继续展开现有素材。",
            ),
        )

    async with database.sessions() as session:
        after_counts = {
            "runs": len((await session.execute(select(Run.id))).scalars().all()),
            "tasks": len((await session.execute(select(Task.id))).scalars().all()),
            "model_calls": len((await session.execute(select(ModelCall.id))).scalars().all()),
        }
    assert after_counts == before_counts


async def test_guided_revision_is_explicit_idempotent_scored_and_style_only(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    (
        parent_run_id,
        style_source_id,
        _supplemental_source_id,
    ) = await _create_completed_parent_with_style(database, service, worker)
    parent_before_plan = await service.get_run(parent_run_id)
    parent_draft = next(
        artifact
        for artifact in parent_before_plan.artifacts
        if artifact.id == parent_before_plan.output_artifact_id
    )
    parent_report = next(
        artifact
        for artifact in parent_before_plan.artifacts
        if artifact.kind == "draft_quality_report"
    )
    style_profile_artifact = next(
        artifact
        for artifact in parent_before_plan.artifacts
        if artifact.kind == "writing_style_profile"
    )
    serialized_profile = json.dumps(
        style_profile_artifact.content_json,
        ensure_ascii=False,
    )
    assert "风格样本里的蓝色旧雨伞只用来验证个人表达通道" not in serialized_profile
    assert '"text"' not in serialized_profile
    original_draft_content = deepcopy(parent_draft.content_json)
    original_report_content = deepcopy(parent_report.content_json)

    async with database.sessions() as session:
        assert len((await session.execute(select(Run))).scalars().all()) == 1

    plan = await service.get_draft_improvement_plan(parent_run_id)
    replayed_plan = await service.get_draft_improvement_plan(parent_run_id)
    assert replayed_plan.artifact.id == plan.artifact.id
    assert replayed_plan.plan == plan.plan
    assert plan.plan.parent_run_id == parent_run_id
    assert plan.plan.parent_draft_artifact_id == parent_draft.id
    assert plan.plan.quality_report_artifact_id == parent_report.id
    assert plan.plan.writing_style_context_available is True
    assert plan.plan.duration.missing_script_character_count > 0
    assert all(
        reference.source_id != style_source_id
        for reference in plan.plan.material.unused_source_refs
    )
    assert (
        len(
            [
                artifact
                for artifact in (await service.get_run(parent_run_id)).artifacts
                if artifact.kind == "draft_improvement_plan"
            ]
        )
        == 1
    )
    assert (
        sum(
            event.type == "workflow.draft_improvement.planned"
            for event in await service.list_events(parent_run_id)
        )
        == 1
    )

    async with database.sessions() as session:
        # Reading/persisting a deterministic Plan must never create a child Run.
        assert len((await session.execute(select(Run))).scalars().all()) == 1

    feedback = await service.submit_draft_feedback(
        parent_run_id,
        feedback=DraftUserFeedbackRequest(
            submission_id="m3.6-human-review-1",
            feedback_origin="human",
            decision="needs_revision",
            overall_rating=3,
            voice_match_rating=2,
            recordability_rating=3,
            usefulness_rating=4,
            tone_fit_rating=3,
            would_record_as_is=False,
            observed_duration_minutes=6.8,
            comment="第二段信息有用，但语气还不像我，希望保留更多自然停顿。",
        ),
    )
    lower_duration = next(
        option.suggested_target_duration_minutes
        for option in plan.plan.options
        if option.kind == "lower_target_duration"
    )
    request = CreateDraftRevisionRequest(
        submission_id="m3.6-revision-1",
        selected_actions=[
            "lower_target_duration",
            "apply_selected_feedback",
        ],
        selected_feedback_artifact_ids=[feedback.artifact.id],
        target_duration_minutes=lower_duration,
        revision_instruction="保留事实边界，开场更直接，第二段改成更自然的口语。",
    )
    created = await service.create_draft_revision(
        parent_run_id,
        request=request,
    )
    assert created.idempotent_replay is False
    assert created.run.workflow_type == "podcast-revision"
    assert created.run.workflow_version == GUIDED_REVISION_WORKFLOW_VERSION
    assert created.run.parent_run_id == parent_run_id
    # The explicit request only queues work; claiming the first task changes
    # the child from queued to running.
    assert created.run.status == "queued"
    assert created.run.current_step == REVISE_PODCAST_DRAFT
    assert created.run.model_call_count == 0
    assert [task.kind for task in created.run.tasks] == [REVISE_PODCAST_DRAFT]

    replay = await service.create_draft_revision(
        parent_run_id,
        request=request,
    )
    assert replay.idempotent_replay is True
    assert replay.run.id == created.run.id
    assert replay.request_artifact_id == created.request_artifact_id

    with pytest.raises(
        DraftRevisionConflict,
        match="submission_id was already used with a different revision request",
    ):
        await service.create_draft_revision(
            parent_run_id,
            request=request.model_copy(
                update={"revision_instruction": "同一个 submission_id 不能改变选择。"}
            ),
        )

    # The parent already spent five calls. A two-call limit still allows the
    # child to run Editor + Reviewer because accounting is scoped per Run.
    worker.max_model_calls_per_run = 2
    assert await worker.run_until_idle() == 2
    child = await service.get_run(created.run.id)
    assert child.status == "succeeded"
    assert child.current_step == "complete"
    assert child.parent_run_id == parent_run_id
    assert child.model_call_count == 2
    assert {call.task_id for call in child.model_calls} == {task.id for task in child.tasks}
    assert {task.kind for task in child.tasks} == {
        REVISE_PODCAST_DRAFT,
        "review_podcast_draft",
    }
    assert {artifact.kind for artifact in child.artifacts}.issuperset(
        {
            f"{REVISE_PODCAST_DRAFT}_result",
            "draft_metrics_report",
            "review_podcast_draft_result",
            "draft_quality_report",
        }
    )

    child_report = await service.get_draft_quality_report(child.id)
    assert child_report.report.scoring_formula_version == STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION
    assert child_report.report.writing_style_context_status == "ready"
    assert child_report.report.model_self_review is not None
    assert {
        dimension.dimension for dimension in child_report.report.model_self_review.dimensions
    }.issuperset({PERSONAL_STYLE_DIMENSION})
    assert child_report.report.requires_human_review is True
    comparison = await service.get_draft_revision_comparison(child.id)
    replayed_comparison = await service.get_draft_revision_comparison(child.id)
    assert replayed_comparison.artifact.id == comparison.artifact.id
    assert replayed_comparison.comparison == comparison.comparison
    assert comparison.comparison.parent.run_id == parent_run_id
    assert comparison.comparison.revision.run_id == child.id
    assert comparison.comparison.automatic_winner_selected is False
    assert comparison.comparison.requires_human_review is True

    child_draft = next(
        artifact for artifact in child.artifacts if artifact.id == child.output_artifact_id
    )
    assert child_draft.kind == f"{REVISE_PODCAST_DRAFT}_result"
    assert child_draft.content_json != original_draft_content
    assert all(
        source_id != style_source_id
        for source_id, _segment_id in _draft_reference_keys(child_draft.content_json)
    )
    assert "风格样本里的蓝色旧雨伞只用来验证个人表达通道" not in json.dumps(
        child_draft.content_json,
        ensure_ascii=False,
    )

    parent_after_revision = await service.get_run(parent_run_id)
    persisted_parent_draft = next(
        artifact for artifact in parent_after_revision.artifacts if artifact.id == parent_draft.id
    )
    persisted_parent_report = next(
        artifact for artifact in parent_after_revision.artifacts if artifact.id == parent_report.id
    )
    assert persisted_parent_draft.content_json == original_draft_content
    assert persisted_parent_report.content_json == original_report_content
    assert parent_after_revision.output_artifact_id == parent_draft.id
    assert parent_after_revision.model_call_count == 5
    assert all(
        source_id != style_source_id
        for source_id, _segment_id in _draft_reference_keys(persisted_parent_draft.content_json)
    )

    async with database.sessions() as session:
        child_tasks = (
            (await session.execute(select(Task).where(Task.run_id == child.id))).scalars().all()
        )
    revision_task = next(task for task in child_tasks if task.kind == REVISE_PODCAST_DRAFT)
    assert "length_recovery_plan" not in revision_task.input_json
    assert revision_task.input_json["selected_feedback"] == [
        {
            "artifact_id": feedback.artifact.id,
            "feedback_origin": "human",
            "decision": "needs_revision",
            "overall_rating": 3,
            "voice_match_rating": 2,
            "recordability_rating": 3,
            "usefulness_rating": 4,
            "tone_fit_rating": 3,
            "would_record_as_is": False,
            "observed_duration_minutes": 6.8,
            "comment": "第二段信息有用，但语气还不像我，希望保留更多自然停顿。",
        }
    ]
    assert revision_task.input_json["writing_style_profile"]["usage"] == "style_only"
    assert {
        segment["source_id"] for segment in revision_task.input_json["writing_style_segments"]
    } == {style_source_id}
    assert {
        segment["source_id"]
        for segment in [
            *revision_task.input_json["initial_source_segments"],
            *revision_task.input_json["supplemental_source_segments"],
        ]
    }.isdisjoint({style_source_id})
    reviewer_task = next(task for task in child_tasks if task.kind == "review_podcast_draft")
    assert all(
        reference["source_id"] != style_source_id
        for reference in reviewer_task.input_json["allowed_source_refs"]
    )
    assert {
        segment["source_id"] for segment in reviewer_task.input_json["writing_style_segments"]
    } == {style_source_id}

    parent_events = await service.list_events(parent_run_id)
    child_events = await service.list_events(child.id)
    assert sum(event.type == "workflow.draft_revision.requested" for event in parent_events) == 1
    assert any(event.type == "workflow.draft_quality.completed" for event in child_events)
    assert sum(event.type == "workflow.draft_revision.compared" for event in child_events) == 1
    private_text = "第二段信息有用，但语气还不像我，希望保留更多自然停顿。"
    assert private_text not in json.dumps(
        [event.model_dump(mode="json") for event in [*parent_events, *child_events]],
        ensure_ascii=False,
    )
