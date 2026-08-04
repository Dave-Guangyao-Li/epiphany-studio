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
from epiphany.models import Artifact, ModelCall, Project, ProjectSource, Run, Source, Task
from epiphany.revision_schemas import (
    LEGACY_DRAFT_REVISION_REQUEST_VERSION,
    PODCAST_REVISION_PATCH_VERSION,
    REVISE_PODCAST_DRAFT,
    CreateDraftRevisionRequest,
    InvalidPodcastRevisionSourceReference,
    PodcastRevisionAddedMaterialUnused,
    PodcastRevisionPatchSchemaError,
    PodcastRevisionRecoveryMaterialUnused,
    PodcastRevisionSchemaError,
    PodcastRevisionTaskInput,
    PodcastRevisionTitleTopicMismatch,
    validate_podcast_revision_output,
)
from epiphany.runtime.orchestrator import (
    DRAFT_AWARE_INTERVIEW_WORKFLOW_VERSION,
    GUIDED_REVISION_WORKFLOW_VERSION,
)
from epiphany.runtime.providers import (
    FakeProvider,
    ProviderResult,
    TaskInvocation,
)
from epiphany.runtime.revision_prompts import build_revision_prompt
from epiphany.runtime.worker import Worker
from epiphany.services import (
    DraftRevisionConflict,
    DraftRevisionNotAllowed,
    RunService,
    SupplementalInterviewPlanNotReady,
)
from epiphany.source_service import SourceService


class _InvalidSupplementalPlannerFake(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        if invocation.kind == "plan_draft_supplemental_interview":
            return ProviderResult(
                content={"questions": []},
                provider=self.name,
                model=self.model,
            )
        return await super().generate(invocation)


class _InvalidReviewerEvidenceFake(FakeProvider):
    """Return one well-shaped Reviewer response with a non-verbatim quote."""

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        result = await super().generate(invocation)
        if invocation.kind != "review_podcast_draft":
            return result
        content = deepcopy(result.content)
        content["dimensions"][0]["evidence"][0]["exact_quote"] = (  # type: ignore[index]
            "这句文字没有出现在初稿里，因此必须被严格证据校验拒绝。"
        )
        return ProviderResult(
            content=content,
            provider=result.provider,
            model=result.model,
        )


class _NoChangeThenGroundedRevisionFake(FakeProvider):
    """Ignore one edit, then honor it through the normal deterministic Fake path."""

    def __init__(self) -> None:
        self.repair_error_codes: list[str | None] = []

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        if invocation.kind == REVISE_PODCAST_DRAFT and invocation.attempt == 1:
            parsed = PodcastRevisionTaskInput.model_validate(invocation.input_json)
            return ProviderResult(
                content=parsed.parent_podcast_draft.model_dump(mode="json"),
                provider=self.name,
                model=self.model,
            )
        if invocation.kind == REVISE_PODCAST_DRAFT:
            self.repair_error_codes.append(invocation.previous_error_code)
        return await super().generate(invocation)


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


def _supplemental_answer_material(
    *,
    round_number: int,
    detail_repetitions: int = 0,
) -> str:
    return "\n\n".join(
        (
            f"这是对第{round_number}轮问题{index}的具体回答。"
            "那天我坐在窗边，钥匙放在桌角，雨刚停，楼道里有人拖着行李箱。"
            "我先关掉云盘，又重新点开录音；听到停顿时没有哭，"
            "只是把手从触控板上移开。后来我给朋友打了电话，说我终于承认"
            "停更不是因为忙，而是怕说得不够好。现在回头看，"
            "这个动作比任何总结都更接近我当时的状态。"
            + "".join(
                (
                    f"补充细节{round_number}-{index}-{detail_index}是："
                    "我记得屏幕右上角的时间、杯子已经凉掉，"
                    "以及自己删掉一句总结后才把真正发生的动作说出来。"
                )
                for detail_index in range(detail_repetitions)
            )
        )
        for index in range(1, 5)
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


async def _constrain_parent_to_partial_unused_material(
    database: Database,
    *,
    parent_run_id: str,
) -> None:
    """Make the v9 supplement bridge independent of Interviewer omissions.

    The production workflow now carries every explicitly selected factual
    segment into Editor/readiness.  These tests still need a deterministic case
    where one grounded recovery attempt cannot reach the duration minimum, so
    bound the *unused* segment text in the persisted Editor bundle to explicit
    short fixture markers.  Cited material and the parent Draft are unchanged;
    the child can reuse a little real in-scope evidence, then must ask the user
    focused questions instead of padding.
    """

    async with database.sessions() as session, session.begin():
        parent = await session.get(Run, parent_run_id)
        assert parent is not None and parent.output_artifact_id is not None
        parent_draft = await session.get(Artifact, parent.output_artifact_id)
        assert parent_draft is not None and parent_draft.task_id is not None
        editor_task = await session.get(Task, parent_draft.task_id)
        assert editor_task is not None

        cited_keys = _spoken_reference_keys(parent_draft.content_json)
        bounded_input = deepcopy(editor_task.input_json)
        bounded_unused_count = 0
        for field_name in ["initial_source_segments", "supplemental_source_segments"]:
            bounded_segments: list[dict[str, object]] = []
            for segment in bounded_input[field_name]:
                bounded_segment = dict(segment)
                key = (
                    str(bounded_segment["source_id"]),
                    str(bounded_segment["source_segment_id"]),
                )
                if key not in cited_keys:
                    bounded_unused_count += 1
                    bounded_segment["text"] = f"受控未展开线索{bounded_unused_count}。"
                bounded_segments.append(bounded_segment)
            bounded_input[field_name] = bounded_segments

        assert bounded_unused_count > 0
        editor_task.input_json = bounded_input


async def test_project_revision_inherits_parent_workspace(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    (
        parent_run_id,
        style_source_id,
        supplemental_source_id,
    ) = await _create_completed_parent_with_style(
        database,
        service,
        worker,
        supplemental_paragraph_count=15,
    )

    async with database.sessions() as session, session.begin():
        parent = await session.get(Run, parent_run_id)
        assert parent is not None
        project = Project(title="Project lineage test")
        session.add(project)
        await session.flush()
        factual_source_ids = list(parent.input_json["source_ids"])
        for source_id in {
            *factual_source_ids,
            style_source_id,
            supplemental_source_id,
        }:
            session.add(ProjectSource(project_id=project.id, source_id=source_id))
        parent.project_id = project.id
        project_id = project.id

    plan = await service.get_draft_improvement_plan(parent_run_id)
    assert plan.plan.duration_resolution == "reuse_unused_material"
    created = await service.create_draft_revision(
        parent_run_id,
        request=CreateDraftRevisionRequest(
            submission_id="project-lineage-revision",
            selected_actions=["reuse_unused_material"],
        ),
    )

    assert created.run.parent_run_id == parent_run_id
    assert created.run.project_id == project_id


async def test_v8_targeted_supplement_creates_v9_child_without_interview_plan(
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
        supplemental_paragraph_count=8,
    )
    parent = await service.get_run(parent_run_id)
    assert parent.workflow_version == GUIDED_REVISION_WORKFLOW_VERSION
    assert all(task.kind != "plan_draft_supplemental_interview" for task in parent.tasks)

    answer_source_id = await _import_source(
        database,
        title="M3.9 v8 初稿后的定向补充回答",
        source_type="voice_note_transcript",
        text=_supplemental_answer_material(round_number=1),
    )
    request = CreateDraftRevisionRequest(
        submission_id="m3.9-v8-targeted-supplement",
        selected_actions=["add_supplemental_material"],
        source_ids=[answer_source_id],
    )
    created = await service.create_draft_revision(parent_run_id, request=request)

    assert created.idempotent_replay is False
    assert created.run.parent_run_id == parent_run_id
    assert created.run.workflow_version == DRAFT_AWARE_INTERVIEW_WORKFLOW_VERSION
    assert created.run.input_json["supplemental_interview_round"] == 0
    assert answer_source_id in created.run.input_json["source_ids"]

    async with database.sessions() as session:
        request_artifact = await session.get(Artifact, created.request_artifact_id)
        revision_task = (
            await session.execute(
                select(Task).where(
                    Task.run_id == created.run.id,
                    Task.kind == REVISE_PODCAST_DRAFT,
                )
            )
        ).scalar_one()
        interview_plan = (
            await session.execute(
                select(Artifact).where(
                    Artifact.run_id == parent_run_id,
                    Artifact.kind == "plan_draft_supplemental_interview_result",
                )
            )
        ).scalar_one_or_none()

    assert request_artifact is not None
    assert request_artifact.content_json["supplemental_interview_plan_artifact_id"] is None
    assert request_artifact.content_json["answered_question_ids"] == []
    assert revision_task.input_json["added_source_ids"] == [answer_source_id]
    assert revision_task.input_json["supplemental_interview_round"] == 0
    assert interview_plan is None

    replayed = await service.create_draft_revision(parent_run_id, request=request)
    assert replayed.idempotent_replay is True
    assert replayed.run.id == created.run.id
    assert replayed.request_artifact_id == created.request_artifact_id


async def test_targeted_supplement_requires_new_grounded_spoken_text_and_repairs_once(
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
        supplemental_paragraph_count=8,
    )
    answer_source_id = await _import_source(
        database,
        title="M3.9 定向回答必须进入口播正文",
        source_type="voice_note_transcript",
        text=_supplemental_answer_material(round_number=1),
    )
    created = await service.create_draft_revision(
        parent_run_id,
        request=CreateDraftRevisionRequest(
            submission_id="m3.9-targeted-answer-contract-and-repair",
            selected_actions=["add_supplemental_material"],
            source_ids=[answer_source_id],
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

    ignored_answer = deepcopy(revision_task.input_json["parent_podcast_draft"])
    ignored_answer["show_notes"]["summary"]["text"] += "（只修改节目简介）"
    with pytest.raises(PodcastRevisionAddedMaterialUnused):
        validate_podcast_revision_output(
            task_input=revision_task.input_json,
            content=ignored_answer,
        )

    prompt = build_revision_prompt(
        task_input=revision_task.input_json,
        max_bundle_chars=100_000,
    )
    repair_prompt = build_revision_prompt(
        task_input=revision_task.input_json,
        max_bundle_chars=100_000,
        repair_attempt=True,
        previous_error_code="podcast_revision_added_material_unused",
    )
    assert "这是对第1轮问题1的具体回答" in prompt.messages[-1]["content"]
    assert "priority_added_source_segments=" in prompt.messages[-1]["content"]
    assert "只改 Show Notes" in prompt.messages[-1]["content"]
    assert "上一版模型输出没有形成可接受的新候选稿" in repair_prompt.messages[-1]["content"]
    assert "没有把用户本轮补充素材写进口播" in repair_prompt.messages[-1]["content"]

    repair_provider = _NoChangeThenGroundedRevisionFake()
    worker.provider = repair_provider
    await worker.run_until_idle()
    completed = await service.get_run(created.run.id)
    assert completed.status == "succeeded"
    completed_revision_task = next(
        task for task in completed.tasks if task.kind == REVISE_PODCAST_DRAFT
    )
    assert completed_revision_task.attempt == 2
    assert any(
        event.type == "task.retry_scheduled"
        and event.payload["error_code"] == "podcast_revision_no_change"
        for event in await service.list_events(completed.id)
    )
    assert repair_provider.repair_error_codes == ["podcast_revision_no_change"]
    completed_draft = next(
        artifact for artifact in completed.artifacts if artifact.id == completed.output_artifact_id
    )
    parent_spoken_texts = {
        text
        for text, _references in _spoken_units(revision_task.input_json["parent_podcast_draft"])
    }
    assert any(
        any(source_id == answer_source_id for source_id, _segment_id in references)
        and text not in parent_spoken_texts
        for text, references in _spoken_units(completed_draft.content_json)
    )


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
    first_priority_ref = next(iter(priority_refs))

    show_notes_only_change = deepcopy(revision_task.input_json["parent_podcast_draft"])
    show_notes_only_change["show_notes"]["summary"]["text"] += "（只调整节目简介）"
    with pytest.raises(PodcastRevisionRecoveryMaterialUnused):
        validate_podcast_revision_output(
            task_input=revision_task.input_json,
            content=show_notes_only_change,
        )

    schema_invalid = deepcopy(revision_task.input_json["parent_podcast_draft"])
    schema_invalid["podcast_script"]["sections"] = []
    with pytest.raises(PodcastRevisionSchemaError):
        validate_podcast_revision_output(
            task_input=revision_task.input_json,
            content=schema_invalid,
        )

    wrong_title = deepcopy(revision_task.input_json["parent_podcast_draft"])
    wrong_title["title"] = "模型擅自改写的标题"
    with pytest.raises(PodcastRevisionTitleTopicMismatch):
        validate_podcast_revision_output(
            task_input=revision_task.input_json,
            content=wrong_title,
        )

    unsupported_reference = deepcopy(revision_task.input_json["parent_podcast_draft"])
    unsupported_reference["podcast_script"]["opening"]["source_refs"] = [
        {"source_id": "src_outside_scope", "source_segment_id": "seg_outside_scope"}
    ]
    with pytest.raises(InvalidPodcastRevisionSourceReference):
        validate_podcast_revision_output(
            task_input=revision_task.input_json,
            content=unsupported_reference,
        )

    valid_patch = {
        "patch_version": PODCAST_REVISION_PATCH_VERSION,
        "append_to_sections": [
            {
                "section_index": 0,
                "paragraphs": [
                    {
                        "text": "这里展开一个父稿尚未讲到、由优先候选支持的具体场景。",
                        "source_refs": [
                            {
                                "source_id": first_priority_ref[0],
                                "source_segment_id": first_priority_ref[1],
                            }
                        ],
                    }
                ],
            }
        ],
        "new_sections": [],
    }
    patch_candidate = validate_podcast_revision_output(
        task_input=revision_task.input_json,
        content=valid_patch,
    )
    assert patch_candidate["title"] == revision_task.input_json["parent_podcast_draft"]["title"]
    assert (
        patch_candidate["show_notes"]
        == revision_task.input_json["parent_podcast_draft"]["show_notes"]
    )
    assert patch_candidate["podcast_script"]["sections"][0]["paragraphs"][-1]["source_refs"] == [
        {
            "source_id": first_priority_ref[0],
            "source_segment_id": first_priority_ref[1],
        }
    ]

    with pytest.raises(PodcastRevisionPatchSchemaError):
        validate_podcast_revision_output(
            task_input=revision_task.input_json,
            content={
                "patch_version": PODCAST_REVISION_PATCH_VERSION,
                "append_to_sections": [
                    {
                        "section_index": 7,
                        "paragraphs": [
                            {
                                "text": "无效的父稿 section 索引。",
                                "source_refs": [
                                    {
                                        "source_id": first_priority_ref[0],
                                        "source_segment_id": first_priority_ref[1],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "new_sections": [],
            },
        )

    prompt = build_revision_prompt(
        task_input=revision_task.input_json,
        max_bundle_chars=100_000,
    )
    repair_prompt = build_revision_prompt(
        task_input=revision_task.input_json,
        max_bundle_chars=100_000,
        repair_attempt=True,
        previous_error_code="podcast_revision_patch_schema_invalid",
    )
    prompt_tail = prompt.messages[-1]["content"]
    factual_text_by_ref = {
        (segment["source_id"], segment["source_segment_id"]): segment["text"]
        for segment in [
            *revision_task.input_json["initial_source_segments"],
            *revision_task.input_json["supplemental_source_segments"],
        ]
    }
    assert "priority_recovery_source_segments=" in prompt_tail
    assert '"patch_version": "podcast_revision_patch_v1"' in prompt_tail
    assert "禁止重新输出 title、podcast_script" in prompt_tail
    assert "只返回 podcast_revision_patch_v1 JSON object" in prompt_tail
    assert factual_text_by_ref[first_priority_ref] in prompt_tail
    assert "只把候选挂到 section metadata 或 Show Notes" in prompt_tail
    assert "priority_recovery_source_segments" in repair_prompt.messages[-1]["content"]
    assert "根对象只能包含 patch_version" in repair_prompt.messages[-1]["content"]
    assert "禁止返回完整 PodcastDraft" in repair_prompt.messages[-1]["content"]
    assert "不得提交无变化版本" in repair_prompt.messages[-1]["content"]

    without_recovery = deepcopy(revision_task.input_json)
    without_recovery.pop("length_recovery_plan")
    legacy_task = PodcastRevisionTaskInput.model_validate(without_recovery)
    assert legacy_task.length_recovery_plan is None
    with pytest.raises(
        PodcastRevisionPatchSchemaError,
        match="only valid for planned length recovery",
    ):
        validate_podcast_revision_output(
            task_input=without_recovery,
            content=valid_patch,
        )

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


async def test_v9_draft_aware_questions_drive_two_bounded_answer_revisions(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    """Exercise the complete Fake loop from grounded recovery to answered questions."""

    database, service, worker = runtime
    (
        parent_run_id,
        _style_source_id,
        _supplemental_source_id,
    ) = await _create_completed_parent_with_style(
        database,
        service,
        worker,
        supplemental_paragraph_count=8,
    )
    await _constrain_parent_to_partial_unused_material(
        database,
        parent_run_id=parent_run_id,
    )
    parent_plan = (await service.get_draft_improvement_plan(parent_run_id)).plan
    assert parent_plan.duration_resolution == "reuse_then_supplement"

    recovered = await service.create_draft_revision(
        parent_run_id,
        request=CreateDraftRevisionRequest(
            submission_id="m3.9-grounded-recovery",
            selected_actions=["reuse_unused_material"],
            revision_instruction="先使用有价值的未展开素材；仍然不足时不要灌水。",
        ),
    )
    assert recovered.run.workflow_version == DRAFT_AWARE_INTERVIEW_WORKFLOW_VERSION
    assert await worker.run_until_idle() == 3
    first_revision = await service.get_run(recovered.run.id)
    assert first_revision.status == "succeeded"
    assert {task.kind for task in first_revision.tasks} == {
        REVISE_PODCAST_DRAFT,
        "review_podcast_draft",
        "plan_draft_supplemental_interview",
    }
    first_draft = next(
        artifact
        for artifact in first_revision.artifacts
        if artifact.id == first_revision.output_artifact_id
    )
    first_plan = await service.get_supplemental_interview_plan(first_revision.id)
    calls_before_read_replay = first_revision.model_call_count
    replayed_plan = await service.get_supplemental_interview_plan(first_revision.id)
    assert replayed_plan.artifact.id == first_plan.artifact.id
    assert (await service.get_run(first_revision.id)).model_call_count == calls_before_read_replay
    assert first_plan.plan.round_number == 1
    assert [question.question_id for question in first_plan.plan.questions] == [
        "q1",
        "q2",
        "q3",
    ]
    assert all(
        question.anchor_quote
        in next(
            anchor.excerpt
            for anchor in first_plan.plan.draft_anchors
            if anchor.anchor_id == question.anchor_id
        )
        for question in first_plan.plan.questions
    )
    with pytest.raises(
        DraftRevisionNotAllowed,
        match="cannot bypass the answered-question path",
    ):
        await service.create_draft_revision(
            first_revision.id,
            request=CreateDraftRevisionRequest(
                submission_id="m3.9-bypass-plan-with-reuse",
                selected_actions=["reuse_unused_material"],
            ),
        )

    first_answer_id = await _import_source(
        database,
        title="M3.9 第一轮定向补充口述",
        source_type="voice_note_transcript",
        text=_supplemental_answer_material(round_number=1, detail_repetitions=5),
    )
    with pytest.raises(
        DraftRevisionNotAllowed,
        match="requires one persisted interview plan",
    ):
        await service.create_draft_revision(
            first_revision.id,
            request=CreateDraftRevisionRequest(
                submission_id="m3.9-bypass-plan",
                selected_actions=["add_supplemental_material"],
                source_ids=[first_answer_id],
            ),
        )
    with pytest.raises(
        DraftRevisionNotAllowed,
        match="question is unavailable",
    ):
        await service.create_draft_revision(
            first_revision.id,
            request=CreateDraftRevisionRequest(
                submission_id="m3.9-unknown-question",
                selected_actions=["add_supplemental_material"],
                source_ids=[first_answer_id],
                supplemental_interview_plan_artifact_id=first_plan.artifact.id,
                answered_question_ids=["q6"],
            ),
        )

    first_answer_revision = await service.create_draft_revision(
        first_revision.id,
        request=CreateDraftRevisionRequest(
            submission_id="m3.9-answer-round-1",
            selected_actions=["add_supplemental_material"],
            source_ids=[first_answer_id],
            supplemental_interview_plan_artifact_id=first_plan.artifact.id,
            answered_question_ids=[question.question_id for question in first_plan.plan.questions],
        ),
    )
    assert await worker.run_until_idle() == 3
    second_revision = await service.get_run(first_answer_revision.run.id)
    second_draft = next(
        artifact
        for artifact in second_revision.artifacts
        if artifact.id == second_revision.output_artifact_id
    )
    assert second_revision.input_json["supplemental_interview_round"] == 1
    assert _spoken_character_count(second_draft.content_json) > _spoken_character_count(
        first_draft.content_json
    )
    second_plan = await service.get_supplemental_interview_plan(second_revision.id)
    assert second_plan.plan.round_number == 2
    assert {question.prompt for question in first_plan.plan.questions}.isdisjoint(
        {question.prompt for question in second_plan.plan.questions}
    )

    second_answer_id = await _import_source(
        database,
        title="M3.9 第二轮定向补充口述",
        source_type="voice_note_transcript",
        text=_supplemental_answer_material(round_number=2, detail_repetitions=5),
    )
    second_answer_revision = await service.create_draft_revision(
        second_revision.id,
        request=CreateDraftRevisionRequest(
            submission_id="m3.9-answer-round-2",
            selected_actions=["add_supplemental_material"],
            source_ids=[second_answer_id],
            supplemental_interview_plan_artifact_id=second_plan.artifact.id,
            answered_question_ids=[question.question_id for question in second_plan.plan.questions],
        ),
    )
    assert await worker.run_until_idle() == 2
    final_revision = await service.get_run(second_answer_revision.run.id)
    final_draft = next(
        artifact
        for artifact in final_revision.artifacts
        if artifact.id == final_revision.output_artifact_id
    )
    minimum_characters = math.ceil(15 * 280 * 0.85)
    assert final_revision.input_json["supplemental_interview_round"] == 2
    assert _spoken_character_count(final_draft.content_json) >= minimum_characters
    assert all(task.kind != "plan_draft_supplemental_interview" for task in final_revision.tasks)
    with pytest.raises(SupplementalInterviewPlanNotReady):
        await service.get_supplemental_interview_plan(final_revision.id)


async def test_v9_planner_validation_failure_preserves_draft_with_fallback_plan(
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
        supplemental_paragraph_count=8,
    )
    await _constrain_parent_to_partial_unused_material(
        database,
        parent_run_id=parent_run_id,
    )
    revision = await service.create_draft_revision(
        parent_run_id,
        request=CreateDraftRevisionRequest(
            submission_id="m3.9-invalid-planner-fallback",
            selected_actions=["reuse_unused_material"],
        ),
    )
    worker.provider = _InvalidSupplementalPlannerFake()
    assert await worker.run_until_idle() == 3

    completed = await service.get_run(revision.run.id)
    assert completed.status == "succeeded"
    assert completed.output_artifact_id is not None
    planner_task = next(
        task for task in completed.tasks if task.kind == "plan_draft_supplemental_interview"
    )
    assert planner_task.status == "failed"

    plan = await service.get_supplemental_interview_plan(completed.id)
    assert plan.plan.generation_mode == "deterministic_fallback"
    assert plan.plan.status == "awaiting_user"
    assert len(plan.plan.questions) == 3
    assert all(question.anchor_quote for question in plan.plan.questions)


async def test_v9_invalid_reviewer_evidence_degrades_and_completes_accepted_draft(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    """Reviewer evidence is advisory once deterministic duration already passes."""

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
    revision = await service.create_draft_revision(
        parent_run_id,
        request=CreateDraftRevisionRequest(
            submission_id="v9-invalid-reviewer-evidence-accepted-duration",
            selected_actions=["reuse_unused_material"],
        ),
    )
    worker.provider = _InvalidReviewerEvidenceFake()

    # Revision + one invalid Reviewer response + one bounded Reviewer repair.
    assert await worker.run_until_idle() == 3
    completed = await service.get_run(revision.run.id)
    assert completed.status == "succeeded"
    assert completed.output_artifact_id is not None
    reviewer = next(task for task in completed.tasks if task.kind == "review_podcast_draft")
    assert reviewer.status == "failed"
    assert reviewer.error_code == "invalid_model_review_evidence"
    assert all(task.kind != "plan_draft_supplemental_interview" for task in completed.tasks)

    draft = next(
        artifact for artifact in completed.artifacts if artifact.id == completed.output_artifact_id
    )
    minimum_characters = math.ceil(15 * 280 * 0.85)
    assert _spoken_character_count(draft.content_json) >= minimum_characters
    report = (await service.get_draft_quality_report(completed.id)).report
    assert report.model_review_status == "unavailable"
    assert report.model_review_unavailable_reason == "invalid_model_review_evidence"
    assert report.decision == "automated_review_incomplete"
    with pytest.raises(SupplementalInterviewPlanNotReady):
        await service.get_supplemental_interview_plan(completed.id)


async def test_v9_invalid_reviewer_evidence_still_queues_readable_supplemental_plan(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    """A broken advisory Reviewer must not block the deterministic duration bridge."""

    database, service, worker = runtime
    (
        parent_run_id,
        _style_source_id,
        _supplemental_source_id,
    ) = await _create_completed_parent_with_style(
        database,
        service,
        worker,
        supplemental_paragraph_count=8,
    )
    await _constrain_parent_to_partial_unused_material(
        database,
        parent_run_id=parent_run_id,
    )
    revision = await service.create_draft_revision(
        parent_run_id,
        request=CreateDraftRevisionRequest(
            submission_id="v9-invalid-reviewer-evidence-short-duration",
            selected_actions=["reuse_unused_material"],
        ),
    )
    worker.provider = _InvalidReviewerEvidenceFake()

    # Revision + two bounded Reviewer attempts + supplemental Interviewer.
    assert await worker.run_until_idle() == 4
    completed = await service.get_run(revision.run.id)
    assert completed.status == "succeeded"
    assert completed.output_artifact_id is not None
    reviewer = next(task for task in completed.tasks if task.kind == "review_podcast_draft")
    assert reviewer.status == "failed"
    assert reviewer.error_code == "invalid_model_review_evidence"
    planner = next(
        task for task in completed.tasks if task.kind == "plan_draft_supplemental_interview"
    )
    assert planner.status == "succeeded"

    draft = next(
        artifact for artifact in completed.artifacts if artifact.id == completed.output_artifact_id
    )
    minimum_characters = math.ceil(15 * 280 * 0.85)
    assert _spoken_character_count(draft.content_json) < minimum_characters
    report = (await service.get_draft_quality_report(completed.id)).report
    assert report.model_review_status == "unavailable"
    assert report.model_review_unavailable_reason == "invalid_model_review_evidence"

    plan = await service.get_supplemental_interview_plan(completed.id)
    assert plan.plan.status == "awaiting_user"
    assert plan.plan.generation_mode == "model"
    assert len(plan.plan.questions) == 3
    assert all(question.prompt and question.anchor_quote for question in plan.plan.questions)


async def test_omitted_version_replays_a_persisted_legacy_revision_request(
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
        supplemental_paragraph_count=8,
    )
    created = await service.create_draft_revision(
        parent_run_id,
        request=CreateDraftRevisionRequest(
            version=LEGACY_DRAFT_REVISION_REQUEST_VERSION,
            submission_id="legacy-omitted-version-replay",
            selected_actions=["reuse_unused_material"],
        ),
    )
    replay_request = CreateDraftRevisionRequest(
        submission_id="legacy-omitted-version-replay",
        selected_actions=["reuse_unused_material"],
    )
    assert "version" not in replay_request.model_fields_set

    replayed = await service.create_draft_revision(
        parent_run_id,
        request=replay_request,
    )

    assert replayed.idempotent_replay is True
    assert replayed.run.id == created.run.id
    assert replayed.request_artifact_id == created.request_artifact_id
    assert replayed.run.workflow_version == GUIDED_REVISION_WORKFLOW_VERSION


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


async def test_revision_rejects_ai_assisted_source_as_inherited_writing_style(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    parent_run_id, style_source_id, _ = await _create_completed_parent_with_style(
        database,
        service,
        worker,
    )
    plan = (await service.get_draft_improvement_plan(parent_run_id)).plan
    lower_duration = next(
        option.suggested_target_duration_minutes
        for option in plan.options
        if option.kind == "lower_target_duration"
    )

    # Simulate legacy/imported provenance becoming visible only after the
    # parent task was frozen.  Revision is a second trust boundary and must not
    # silently inherit AI text as the user's identity sample.
    async with database.sessions() as session, session.begin():
        source = await session.get(Source, style_source_id)
        assert source is not None
        source.metadata_json = {**source.metadata_json, "origin": "ai_assisted"}

    with pytest.raises(
        DraftRevisionNotAllowed,
        match="AI-assisted Sources cannot be used as writing-style samples",
    ):
        await service.create_draft_revision(
            parent_run_id,
            request=CreateDraftRevisionRequest(
                submission_id="reject-ai-assisted-inherited-style",
                selected_actions=["lower_target_duration"],
                target_duration_minutes=lower_duration,
            ),
        )


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
    assert created.run.workflow_version == DRAFT_AWARE_INTERVIEW_WORKFLOW_VERSION
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
