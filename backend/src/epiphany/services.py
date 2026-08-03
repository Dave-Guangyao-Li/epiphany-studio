from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from epiphany.db import Database
from epiphany.draft_feedback_schemas import (
    DraftUserFeedback,
    DraftUserFeedbackRecord,
    DraftUserFeedbackRequest,
    DraftUserFeedbackResponse,
)
from epiphany.draft_improvement import (
    DraftImprovementPlanInputError,
    build_draft_improvement_plan,
)
from epiphany.draft_quality_markdown import render_draft_quality_markdown
from epiphany.draft_quality_schemas import (
    DraftQualityReport,
    DraftQualityReportRecord,
)
from epiphany.editor_schemas import (
    BUILD_PODCAST_DRAFT,
    MAX_EDITOR_SUPPLEMENTAL_SEGMENTS,
    PodcastDraftTaskInput,
    editor_output_reference_keys,
)
from epiphany.episode_markdown import (
    render_podcast_draft_markdown,
    render_show_notes_markdown,
)
from epiphany.events import append_event
from epiphany.human_input_schemas import (
    INTERVIEW_SCAFFOLD_CHECKPOINT,
    MATERIAL_READINESS_CHECKPOINT,
)
from epiphany.ids import new_id, stable_id
from epiphany.interview_markdown import (
    SourceCitation,
    interview_scaffold_reference_keys,
    render_interview_scaffold_markdown,
)
from epiphany.interview_schemas import (
    BUILD_INTERVIEW_SCAFFOLD,
    InterviewScaffoldOutput,
)
from epiphany.material_readiness import (
    ReadinessFollowUpQuestion,
    assess_material_readiness,
)
from epiphany.models import Artifact, Event, Project, ProjectSource, Run, Source, Task
from epiphany.project_service import ProjectNotFound
from epiphany.research_schemas import EpisodeResearchPayload
from epiphany.revision_schemas import (
    LEGACY_DRAFT_REVISION_REQUEST_VERSION,
    REVISE_PODCAST_DRAFT,
    CreateDraftRevisionRequest,
    CreateDraftRevisionResponse,
    DraftImprovementPlan,
    DraftImprovementPlanRecord,
    DraftRevisionComparison,
    DraftRevisionComparisonRecord,
    DraftRevisionRequestRecord,
    PodcastRevisionTaskInput,
    build_draft_length_recovery_plan,
    build_draft_revision_candidate_summary,
    build_draft_revision_comparison,
    duration_character_bounds,
)
from epiphany.runtime.orchestrator import (
    DRAFT_AWARE_INTERVIEW_WORKFLOW_VERSION,
    EDITOR_RESEARCH_WORKFLOW_VERSION,
    GUIDED_REVISION_WORKFLOW_VERSION,
    INTERVIEW_RESEARCH_WORKFLOW_VERSION,
    MATERIAL_READINESS_WORKFLOW_VERSION,
    MAX_SUPPLEMENTAL_INTERVIEW_ROUNDS,
    QUALITY_REVIEW_WORKFLOW_VERSION,
    QUALITY_REVIEW_WORKFLOW_VERSIONS,
    Orchestrator,
)
from epiphany.schemas import (
    ArtifactView,
    EventView,
    ModelCallView,
    ResumeRunResponse,
    RunSummaryView,
    RunView,
    TaskView,
)
from epiphany.state_machine import (
    RunStatus,
    TaskStatus,
    validate_run_transition,
    validate_task_transition,
)
from epiphany.supplemental_interview_schemas import (
    PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW,
    SupplementalInterviewPlan,
    SupplementalInterviewPlanRecord,
)
from epiphany.writing_style import build_writing_style_profile
from epiphany.writing_style_schemas import WritingStyleProfile

logger = logging.getLogger("epiphany.run_service")


class RunNotFound(LookupError):
    pass


class RunAlreadyTerminal(ValueError):
    pass


class InvalidRunPayload(ValueError):
    pass


class RunSourceNotFound(LookupError):
    pass


class ProjectSourceNotLinked(ValueError):
    pass


class ProjectRunConflict(ValueError):
    pass


@dataclass(frozen=True)
class CreateProjectRunResult:
    run: RunView
    idempotent_replay: bool


class InterviewScaffoldExportNotReady(ValueError):
    pass


class PodcastDraftExportNotReady(ValueError):
    pass


class RunResumeNotAllowed(ValueError):
    pass


class RunResumeConflict(ValueError):
    pass


class DraftFeedbackNotAllowed(ValueError):
    pass


class DraftFeedbackConflict(ValueError):
    pass


class DraftQualityReportNotReady(ValueError):
    pass


class DraftImprovementPlanNotReady(ValueError):
    pass


class DraftRevisionNotAllowed(ValueError):
    pass


class DraftRevisionConflict(ValueError):
    pass


class DraftRevisionComparisonNotReady(ValueError):
    pass


class SupplementalInterviewPlanNotReady(ValueError):
    pass


class RunService:
    def __init__(self, database: Database, orchestrator: Orchestrator) -> None:
        self.database = database
        self.orchestrator = orchestrator
        # The MVP is explicitly single-process. Every Run mutation that can
        # compete at a human checkpoint shares this lock. Cancel can validly
        # follow a successful v4 Resume while the Editor is queued, but the
        # mutation order is deterministic and no half-written submission can
        # escape. The Artifact unique key remains the durable duplicate-data
        # guard for Resume in SQLite. Cross-process replay semantics are a later
        # deployment concern.
        self._run_mutation_lock = asyncio.Lock()

    async def _load_writing_style_task_fields(
        self,
        session: Any,
        *,
        run: Run,
    ) -> dict[str, Any]:
        """Hydrate only the profile-selected sample text for Editor/Reviewer tasks."""

        raw_profile = run.input_json.get("writing_style_profile")
        if raw_profile is None:
            return {}
        try:
            profile = WritingStyleProfile.model_validate(raw_profile)
        except ValidationError as error:
            raise RunResumeNotAllowed("writing-style profile is invalid") from error
        source_ids = _stable_unique([segment.source_id for segment in profile.selected_segments])
        sources = (
            (
                await session.execute(
                    select(Source)
                    .where(Source.id.in_(source_ids))
                    .options(selectinload(Source.segments))
                )
            )
            .scalars()
            .all()
            if source_ids
            else []
        )
        segments_by_key = {
            (source.id, segment.id): segment for source in sources for segment in source.segments
        }
        missing_refs = [
            (reference.source_id, reference.source_segment_id)
            for reference in profile.selected_segments
            if (reference.source_id, reference.source_segment_id) not in segments_by_key
        ]
        if missing_refs:
            raise RunResumeNotAllowed("writing-style sample material is unavailable")
        return {
            "writing_style_profile": profile.model_dump(mode="json"),
            "writing_style_segments": [
                {
                    "source_id": reference.source_id,
                    "source_segment_id": reference.source_segment_id,
                    "position": reference.position,
                    "text": segments_by_key[
                        (reference.source_id, reference.source_segment_id)
                    ].text,
                }
                for reference in profile.selected_segments
            ],
        }

    async def create_run(
        self,
        *,
        workflow_type: str,
        payload: dict[str, object],
        project_id: str | None = None,
        submission_id: str | None = None,
        request_fingerprint: str | None = None,
    ) -> RunView:
        async with self.database.sessions() as session, session.begin():
            if project_id is not None and await session.get(Project, project_id) is None:
                raise ProjectNotFound(project_id)
            research_source_segments: list[dict[str, str]] | None = None
            writing_style_profile: WritingStyleProfile | None = None
            if workflow_type == "episode-research":
                try:
                    research_payload = EpisodeResearchPayload.model_validate(payload)
                except ValidationError as error:
                    raise InvalidRunPayload("invalid episode-research payload") from error

                style_source_ids = (
                    [
                        sample.source_id
                        for sample in research_payload.writing_style_reference.samples
                    ]
                    if research_payload.writing_style_reference is not None
                    else []
                )
                source_ids_to_load = [*research_payload.source_ids, *style_source_ids]
                sources = (
                    (
                        await session.execute(
                            select(Source)
                            .where(Source.id.in_(source_ids_to_load))
                            .options(selectinload(Source.segments))
                        )
                    )
                    .scalars()
                    .all()
                )
                sources_by_id = {source.id: source for source in sources}
                missing_source_ids = [
                    source_id
                    for source_id in research_payload.source_ids
                    if source_id not in sources_by_id
                ]
                if missing_source_ids:
                    raise RunSourceNotFound(missing_source_ids[0])
                missing_style_source_ids = [
                    source_id for source_id in style_source_ids if source_id not in sources_by_id
                ]
                if missing_style_source_ids:
                    raise RunSourceNotFound(missing_style_source_ids[0])
                if project_id is not None:
                    linked_source_ids = set(
                        (
                            await session.execute(
                                select(ProjectSource.source_id).where(
                                    ProjectSource.project_id == project_id,
                                    ProjectSource.source_id.in_(source_ids_to_load),
                                )
                            )
                        ).scalars()
                    )
                    unlinked_source_ids = [
                        source_id
                        for source_id in source_ids_to_load
                        if source_id not in linked_source_ids
                    ]
                    if unlinked_source_ids:
                        raise ProjectSourceNotLinked(unlinked_source_ids[0])

                payload = research_payload.model_dump(mode="json")
                writing_style_profile = build_writing_style_profile(
                    reference=research_payload.writing_style_reference,
                    source_segments=[
                        {
                            "source_id": source.id,
                            "source_segment_id": segment.id,
                            "position": segment.position,
                            "text": segment.text,
                        }
                        for source_id in style_source_ids
                        for source in [sources_by_id[source_id]]
                        for segment in sorted(source.segments, key=lambda item: item.position)
                    ],
                )
                if writing_style_profile is not None:
                    payload["writing_style_profile"] = writing_style_profile.model_dump(mode="json")
                research_source_segments = [
                    {
                        "source_id": source.id,
                        "source_segment_id": segment.id,
                        "text": segment.text,
                    }
                    for source_id in research_payload.source_ids
                    for source in [sources_by_id[source_id]]
                    for segment in sorted(source.segments, key=lambda item: item.position)
                ]

            initial_step = (
                "research_fan_out" if workflow_type == "episode-research" else "prepare_sources"
            )
            workflow_version = (
                (
                    (
                        QUALITY_REVIEW_WORKFLOW_VERSION
                        if research_payload.draft_quality is not None
                        and research_payload.draft_quality.enabled
                        else MATERIAL_READINESS_WORKFLOW_VERSION
                    )
                    if research_payload.creative_brief is not None
                    else EDITOR_RESEARCH_WORKFLOW_VERSION
                )
                if workflow_type == "episode-research"
                else "v1"
            )
            run = Run(
                project_id=project_id,
                submission_id=submission_id,
                request_fingerprint=request_fingerprint,
                workflow_type=workflow_type,
                workflow_version=workflow_version,
                status=RunStatus.QUEUED,
                current_step=initial_step,
                input_json=payload,
            )
            session.add(run)
            await session.flush()
            if writing_style_profile is not None:
                style_artifact = Artifact(
                    run_id=run.id,
                    task_id=None,
                    kind="writing_style_profile",
                    content_json=writing_style_profile.model_dump(mode="json"),
                    idempotency_key=f"writing-style-profile:{run.id}",
                )
                session.add(style_artifact)
                await session.flush()
                await append_event(
                    session,
                    run_id=run.id,
                    event_type="workflow.writing_style_profile.created",
                    payload={
                        "artifact_id": style_artifact.id,
                        "readiness_status": writing_style_profile.readiness.status,
                        "source_count": writing_style_profile.stats.source_count,
                        "segment_count": writing_style_profile.stats.segment_count,
                        "non_whitespace_char_count": (
                            writing_style_profile.stats.non_whitespace_char_count
                        ),
                    },
                )
            await append_event(
                session,
                run_id=run.id,
                event_type="run.created",
                payload={
                    "workflow_type": workflow_type,
                    "workflow_version": run.workflow_version,
                },
            )
            await self.orchestrator.start_run(
                session,
                run,
                research_source_segments=research_source_segments,
            )
            run_id = run.id

        logger.info(
            "Run created",
            extra={
                "event": "run.created",
                "run_id": run_id,
            },
        )
        return await self.get_run(run_id)

    async def create_project_run(
        self,
        *,
        project_id: str,
        submission_id: str,
        workflow_type: str,
        payload: dict[str, object],
    ) -> CreateProjectRunResult:
        fingerprint = _project_run_request_fingerprint(
            workflow_type=workflow_type,
            payload=payload,
        )
        async with self._run_mutation_lock:
            async with self.database.sessions() as session:
                if await session.get(Project, project_id) is None:
                    raise ProjectNotFound(project_id)
                existing = (
                    await session.execute(
                        select(Run).where(
                            Run.project_id == project_id,
                            Run.submission_id == submission_id,
                        )
                    )
                ).scalar_one_or_none()
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise ProjectRunConflict(
                        "submission_id was already used with a different Run request"
                    )
                return CreateProjectRunResult(
                    run=await self.get_run(existing.id),
                    idempotent_replay=True,
                )

            run = await self.create_run(
                workflow_type=workflow_type,
                payload=payload,
                project_id=project_id,
                submission_id=submission_id,
                request_fingerprint=fingerprint,
            )
            return CreateProjectRunResult(run=run, idempotent_replay=False)

    async def get_run(self, run_id: str) -> RunView:
        async with self.database.sessions() as session:
            statement = (
                select(Run)
                .where(Run.id == run_id)
                .options(
                    selectinload(Run.tasks),
                    selectinload(Run.artifacts),
                    selectinload(Run.model_calls),
                )
            )
            run = (await session.execute(statement)).scalar_one_or_none()
            if run is None:
                raise RunNotFound(run_id)

            tasks = sorted(run.tasks, key=lambda item: (item.created_at, item.id))
            artifacts = sorted(run.artifacts, key=lambda item: (item.created_at, item.id))
            model_calls = sorted(
                run.model_calls,
                key=lambda item: (item.started_at, item.id),
            )
            return RunView(
                id=run.id,
                project_id=run.project_id,
                parent_run_id=run.parent_run_id,
                workflow_type=run.workflow_type,
                workflow_version=run.workflow_version,
                status=run.status,
                current_step=run.current_step,
                input_json=run.input_json,
                output_artifact_id=run.output_artifact_id,
                model_call_count=run.model_call_count,
                cancel_requested_at=run.cancel_requested_at,
                created_at=run.created_at,
                updated_at=run.updated_at,
                tasks=[TaskView.model_validate(task) for task in tasks],
                artifacts=[ArtifactView.model_validate(artifact) for artifact in artifacts],
                model_calls=[
                    ModelCallView.model_validate(model_call) for model_call in model_calls
                ],
            )

    async def list_runs(
        self,
        *,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[RunSummaryView]:
        async with self.database.sessions() as session:
            if project_id is not None and await session.get(Project, project_id) is None:
                raise ProjectNotFound(project_id)
            statement = select(Run)
            if project_id is not None:
                statement = statement.where(Run.project_id == project_id)
            runs = (
                (
                    await session.execute(
                        statement.order_by(Run.created_at.desc(), Run.id).limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [RunSummaryView.model_validate(run) for run in runs]

    async def list_events(self, run_id: str, *, after: int = 0) -> list[EventView]:
        async with self.database.sessions() as session:
            if await session.get(Run, run_id) is None:
                raise RunNotFound(run_id)
            statement = (
                select(Event)
                .where(Event.run_id == run_id, Event.sequence > after)
                .order_by(Event.sequence)
            )
            events = (await session.execute(statement)).scalars().all()
            return [EventView.model_validate(event) for event in events]

    async def submit_draft_feedback(
        self,
        run_id: str,
        *,
        feedback: DraftUserFeedbackRequest,
    ) -> DraftUserFeedbackResponse:
        """Append one idempotent user assessment without changing the Run."""

        async with self._run_mutation_lock:
            async with self.database.sessions() as session, session.begin():
                run = await session.get(Run, run_id)
                if run is None:
                    raise RunNotFound(run_id)
                if run.status != RunStatus.SUCCEEDED or run.output_artifact_id is None:
                    raise DraftFeedbackNotAllowed(
                        "draft feedback requires a succeeded Run with a podcast draft"
                    )

                draft = await session.get(Artifact, run.output_artifact_id)
                if (
                    draft is None
                    or draft.run_id != run.id
                    or draft.kind
                    not in {
                        f"{BUILD_PODCAST_DRAFT}_result",
                        f"{REVISE_PODCAST_DRAFT}_result",
                    }
                ):
                    raise DraftFeedbackNotAllowed(
                        "draft feedback requires a succeeded Run with a podcast draft"
                    )

                content = DraftUserFeedback(
                    submission_id=feedback.submission_id,
                    draft_artifact_id=draft.id,
                    feedback_origin=feedback.feedback_origin,
                    human_signal_eligible=feedback.feedback_origin == "human",
                    decision=feedback.decision,
                    overall_rating=feedback.overall_rating,
                    voice_match_rating=feedback.voice_match_rating,
                    recordability_rating=feedback.recordability_rating,
                    usefulness_rating=feedback.usefulness_rating,
                    tone_fit_rating=feedback.tone_fit_rating,
                    would_record_as_is=feedback.would_record_as_is,
                    observed_duration_minutes=feedback.observed_duration_minutes,
                    comment=feedback.comment,
                )
                content_json = content.model_dump(mode="json")
                feedback_key = stable_id(
                    "feedback",
                    f"{run.id}:{feedback.submission_id}",
                )
                idempotency_key = f"draft-feedback:{feedback_key}"
                artifact = (
                    await session.execute(
                        select(Artifact).where(Artifact.idempotency_key == idempotency_key)
                    )
                ).scalar_one_or_none()
                idempotent_replay = artifact is not None
                if artifact is not None:
                    try:
                        existing = DraftUserFeedback.model_validate(artifact.content_json)
                    except ValidationError as error:
                        raise DraftFeedbackConflict(
                            "existing feedback artifact is invalid"
                        ) from error
                    if existing.model_dump(mode="json") != content_json:
                        raise DraftFeedbackConflict(
                            "submission_id was already used with different feedback"
                        )
                else:
                    artifact = Artifact(
                        run_id=run.id,
                        task_id=None,
                        kind="draft_user_feedback",
                        content_json=content_json,
                        idempotency_key=idempotency_key,
                    )
                    session.add(artifact)
                    await session.flush()
                    await append_event(
                        session,
                        run_id=run.id,
                        event_type="workflow.draft_quality.feedback_recorded",
                        payload={
                            "feedback_artifact_id": artifact.id,
                            "feedback_origin": content.feedback_origin,
                            "human_signal_eligible": content.human_signal_eligible,
                            "overall_rating": content.overall_rating,
                            "feedback_decision": content.decision,
                            "would_record_as_is": content.would_record_as_is,
                        },
                    )
                artifact_view = ArtifactView.model_validate(artifact)

        logger.info(
            (
                "Draft feedback replay returned existing artifact"
                if idempotent_replay
                else "Draft feedback recorded"
            ),
            extra={
                "event": (
                    "workflow.draft_quality.feedback_replayed"
                    if idempotent_replay
                    else "workflow.draft_quality.feedback_recorded"
                ),
                "run_id": run_id,
                "artifact_id": artifact_view.id,
                "feedback_origin": content.feedback_origin,
                "feedback_rating": content.overall_rating,
                "feedback_decision": content.decision,
                "human_signal_eligible": content.human_signal_eligible,
            },
        )
        return DraftUserFeedbackResponse(
            idempotent_replay=idempotent_replay,
            feedback=content,
            artifact=artifact_view,
        )

    async def list_draft_feedback(
        self,
        run_id: str,
    ) -> list[DraftUserFeedbackRecord]:
        async with self.database.sessions() as session:
            if await session.get(Run, run_id) is None:
                raise RunNotFound(run_id)
            artifacts = (
                (
                    await session.execute(
                        select(Artifact)
                        .where(
                            Artifact.run_id == run_id,
                            Artifact.kind == "draft_user_feedback",
                        )
                        .order_by(Artifact.created_at, Artifact.id)
                    )
                )
                .scalars()
                .all()
            )
            try:
                return [
                    DraftUserFeedbackRecord(
                        feedback=DraftUserFeedback.model_validate(artifact.content_json),
                        artifact=ArtifactView.model_validate(artifact),
                    )
                    for artifact in artifacts
                ]
            except ValidationError as error:
                raise DraftFeedbackConflict("persisted feedback artifact is invalid") from error

    async def get_draft_quality_report(
        self,
        run_id: str,
    ) -> DraftQualityReportRecord:
        async with self.database.sessions() as session:
            if await session.get(Run, run_id) is None:
                raise RunNotFound(run_id)
            artifact = (
                await session.execute(
                    select(Artifact)
                    .where(
                        Artifact.run_id == run_id,
                        Artifact.kind == "draft_quality_report",
                    )
                    .order_by(Artifact.created_at.desc(), Artifact.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if artifact is None:
                raise DraftQualityReportNotReady("draft quality report is not ready")
            try:
                return DraftQualityReportRecord(
                    report=DraftQualityReport.model_validate(artifact.content_json),
                    artifact=ArtifactView.model_validate(artifact),
                )
            except ValidationError as error:
                raise DraftQualityReportNotReady(
                    "persisted draft quality report is invalid"
                ) from error

    async def get_draft_improvement_plan(
        self,
        run_id: str,
    ) -> DraftImprovementPlanRecord:
        """Return or deterministically create the plan for one immutable Draft."""

        async with self._run_mutation_lock:
            async with self.database.sessions() as session, session.begin():
                run = await session.get(Run, run_id)
                if run is None:
                    raise RunNotFound(run_id)
                if run.status != RunStatus.SUCCEEDED or run.output_artifact_id is None:
                    raise DraftImprovementPlanNotReady(
                        "draft improvement plan requires a succeeded quality Run"
                    )

                draft = await session.get(Artifact, run.output_artifact_id)
                accepted_draft_kinds = {
                    f"{BUILD_PODCAST_DRAFT}_result",
                    f"{REVISE_PODCAST_DRAFT}_result",
                }
                if (
                    draft is None
                    or draft.run_id != run.id
                    or draft.kind not in accepted_draft_kinds
                    or draft.task_id is None
                ):
                    raise DraftImprovementPlanNotReady(
                        "Run output is not a supported podcast Draft"
                    )
                report = (
                    await session.execute(
                        select(Artifact)
                        .where(
                            Artifact.run_id == run.id,
                            Artifact.kind == "draft_quality_report",
                        )
                        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if report is None:
                    raise DraftImprovementPlanNotReady("draft quality report is not ready")
                editor_task = await session.get(Task, draft.task_id)
                if editor_task is None:
                    raise DraftImprovementPlanNotReady("Draft task provenance is unavailable")

                plan_key = (
                    f"draft-improvement:{run.id}:{draft.id}:{report.id}:"
                    f"{DraftImprovementPlan.model_fields['schema_version'].default}"
                )
                artifact = (
                    await session.execute(
                        select(Artifact).where(Artifact.idempotency_key == plan_key)
                    )
                ).scalar_one_or_none()
                if artifact is None:
                    draft_content = {
                        key: value
                        for key, value in draft.content_json.items()
                        if key != "_execution"
                    }
                    try:
                        plan = build_draft_improvement_plan(
                            parent_run_id=run.id,
                            parent_draft_artifact_id=draft.id,
                            quality_report_artifact_id=report.id,
                            editor_task_input=_base_editor_input(editor_task.input_json),
                            podcast_draft=draft_content,
                            quality_report=report.content_json,
                            interview_scaffold=editor_task.input_json["interview_scaffold"],
                            writing_style_context_available=(
                                _writing_style_context_is_ready(editor_task.input_json)
                            ),
                            prior_length_recovery_attempted=(
                                draft.kind == f"{REVISE_PODCAST_DRAFT}_result"
                                and "reuse_unused_material"
                                in editor_task.input_json.get("selected_actions", [])
                            ),
                        )
                    except (DraftImprovementPlanInputError, KeyError) as error:
                        raise DraftImprovementPlanNotReady(
                            "workflow artifacts cannot produce a safe improvement plan"
                        ) from error
                    artifact = Artifact(
                        run_id=run.id,
                        task_id=editor_task.id,
                        kind="draft_improvement_plan",
                        content_json=plan.model_dump(mode="json"),
                        idempotency_key=plan_key,
                    )
                    session.add(artifact)
                    await session.flush()
                    minimum_characters, _maximum_characters = duration_character_bounds(
                        plan.duration.target_script_character_count
                    )
                    await append_event(
                        session,
                        run_id=run.id,
                        task_id=editor_task.id,
                        event_type="workflow.draft_improvement.planned",
                        payload={
                            "artifact_id": artifact.id,
                            "draft_artifact_id": draft.id,
                            "quality_report_artifact_id": report.id,
                            "duration_resolution": plan.duration_resolution,
                            "missing_script_character_count": (
                                plan.duration.missing_script_character_count
                            ),
                            "missing_to_minimum_character_count": max(
                                0,
                                minimum_characters - plan.duration.actual_script_character_count,
                            ),
                            "unused_factual_segment_count": (
                                plan.material.unused_factual_segment_count
                            ),
                            "targeted_question_count": len(plan.targeted_questions),
                            "writing_style_context_available": (
                                plan.writing_style_context_available
                            ),
                            "prior_length_recovery_attempted": (
                                plan.prior_length_recovery_attempted
                            ),
                        },
                    )
                try:
                    plan = DraftImprovementPlan.model_validate(artifact.content_json)
                except ValidationError as error:
                    raise DraftImprovementPlanNotReady(
                        "persisted draft improvement plan is invalid"
                    ) from error
                artifact_view = ArtifactView.model_validate(artifact)

        minimum_characters, _maximum_characters = duration_character_bounds(
            plan.duration.target_script_character_count
        )
        logger.info(
            "Draft improvement plan ready",
            extra={
                "event": "workflow.draft_improvement.planned",
                "run_id": run_id,
                "artifact_id": artifact_view.id,
                "duration_resolution": plan.duration_resolution,
                "missing_script_character_count": (plan.duration.missing_script_character_count),
                "missing_to_minimum_character_count": max(
                    0,
                    minimum_characters - plan.duration.actual_script_character_count,
                ),
                "unused_factual_segment_count": (plan.material.unused_factual_segment_count),
                "prior_length_recovery_attempted": (plan.prior_length_recovery_attempted),
            },
        )
        return DraftImprovementPlanRecord(plan=plan, artifact=artifact_view)

    async def get_supplemental_interview_plan(
        self,
        run_id: str,
    ) -> SupplementalInterviewPlanRecord:
        """Read one already-persisted latest-Draft interview plan.

        This read path never invokes a Provider. Question generation is owned by
        the durable planner Task, while this method only validates and exposes
        its committed Artifact.
        """

        async with self.database.sessions() as session:
            run = await session.get(Run, run_id)
            if run is None:
                raise RunNotFound(run_id)
            if run.status != RunStatus.SUCCEEDED or run.output_artifact_id is None:
                raise SupplementalInterviewPlanNotReady(
                    "supplemental interview plan requires a succeeded Draft Run"
                )
            artifact = (
                await session.execute(
                    select(Artifact)
                    .where(
                        Artifact.run_id == run.id,
                        Artifact.kind == f"{PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW}_result",
                    )
                    .order_by(Artifact.created_at.desc(), Artifact.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if artifact is None or artifact.task_id is None:
                raise SupplementalInterviewPlanNotReady("supplemental interview plan is not ready")
            task = await session.get(Task, artifact.task_id)
            try:
                plan = SupplementalInterviewPlan.model_validate(
                    {
                        key: value
                        for key, value in artifact.content_json.items()
                        if key != "_execution"
                    }
                )
            except ValidationError as error:
                raise SupplementalInterviewPlanNotReady(
                    "persisted supplemental interview plan is invalid"
                ) from error
            task_status_is_valid = task is not None and (
                task.status == TaskStatus.SUCCEEDED
                or (
                    task.status == TaskStatus.FAILED
                    and plan.generation_mode == "deterministic_fallback"
                )
            )
            if (
                task is None
                or task.run_id != run.id
                or task.kind != PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW
                or not task_status_is_valid
                or task.output_artifact_id != artifact.id
            ):
                raise SupplementalInterviewPlanNotReady(
                    "supplemental interview plan provenance is unavailable"
                )
            if (
                plan.draft_artifact_id != run.output_artifact_id
                or plan.round_number > MAX_SUPPLEMENTAL_INTERVIEW_ROUNDS
            ):
                raise SupplementalInterviewPlanNotReady(
                    "supplemental interview plan does not target the latest Draft"
                )
            artifact_view = ArtifactView.model_validate(artifact)

        logger.info(
            "Supplemental interview plan ready",
            extra={
                "event": "workflow.draft_supplemental_interview.plan_read",
                "run_id": run_id,
                "artifact_id": artifact_view.id,
                "draft_artifact_id": plan.draft_artifact_id,
                "round_number": plan.round_number,
                "question_count": len(plan.questions),
            },
        )
        return SupplementalInterviewPlanRecord(plan=plan, artifact=artifact_view)

    async def create_draft_revision(
        self,
        parent_run_id: str,
        *,
        request: CreateDraftRevisionRequest,
    ) -> CreateDraftRevisionResponse:
        """Create exactly one explicit child Run; never mutate the parent Draft."""

        plan_record = await self.get_draft_improvement_plan(parent_run_id)
        request_key = stable_id(
            "revision",
            f"{parent_run_id}:{request.submission_id}",
        )
        idempotency_key = f"draft-revision-request:{request_key}"
        idempotent_replay = False

        async with self._run_mutation_lock:
            async with self.database.sessions() as session, session.begin():
                existing = (
                    await session.execute(
                        select(Artifact).where(Artifact.idempotency_key == idempotency_key)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    try:
                        existing_record = DraftRevisionRequestRecord.model_validate(
                            existing.content_json
                        )
                    except ValidationError as error:
                        raise DraftRevisionConflict(
                            "persisted revision request is invalid"
                        ) from error
                    if not _revision_request_matches(
                        request=request,
                        record=existing_record,
                        parent_run_id=parent_run_id,
                        plan_artifact_id=plan_record.artifact.id,
                    ):
                        raise DraftRevisionConflict(
                            "submission_id was already used with a different revision request"
                        )
                    child = await session.get(Run, existing_record.child_run_id)
                    if child is None:
                        raise DraftRevisionConflict(
                            "revision request references a missing child Run"
                        )
                    request_artifact_id = existing.id
                    child_run_id = child.id
                    idempotent_replay = True
                else:
                    parent = await session.get(Run, parent_run_id)
                    if (
                        parent is None
                        or parent.status != RunStatus.SUCCEEDED
                        or parent.output_artifact_id is None
                    ):
                        if parent is None:
                            raise RunNotFound(parent_run_id)
                        raise DraftRevisionNotAllowed(
                            "revision requires a succeeded parent quality Run"
                        )
                    parent_draft = await session.get(Artifact, parent.output_artifact_id)
                    if (
                        parent_draft is None
                        or parent_draft.kind
                        not in {
                            f"{BUILD_PODCAST_DRAFT}_result",
                            f"{REVISE_PODCAST_DRAFT}_result",
                        }
                        or parent_draft.task_id is None
                    ):
                        raise DraftRevisionNotAllowed(
                            "parent output is not a supported podcast Draft"
                        )
                    parent_editor_task = await session.get(Task, parent_draft.task_id)
                    if parent_editor_task is None:
                        raise DraftRevisionNotAllowed("parent Draft task provenance is unavailable")
                    parent_report = await session.get(
                        Artifact,
                        plan_record.plan.quality_report_artifact_id,
                    )
                    if (
                        parent_report is None
                        or parent_report.run_id != parent.id
                        or parent_report.kind != "draft_quality_report"
                    ):
                        raise DraftRevisionNotAllowed(
                            "parent quality report provenance is unavailable"
                        )

                    parent_supplemental_round = _supplemental_interview_round(parent.input_json)
                    child_supplemental_round = parent_supplemental_round
                    interview_plan_artifact: Artifact | None = None
                    interview_plan: SupplementalInterviewPlan | None = None
                    interview_provenance_present = bool(
                        request.supplemental_interview_plan_artifact_id
                        or request.answered_question_ids
                    )
                    supplemental_action_selected = (
                        "add_supplemental_material" in request.selected_actions
                    )
                    reuse_action_selected = "reuse_unused_material" in request.selected_actions
                    persisted_interview_plan_artifact = (
                        await session.execute(
                            select(Artifact)
                            .where(
                                Artifact.run_id == parent.id,
                                Artifact.kind == f"{PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW}_result",
                            )
                            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if (
                        parent.workflow_version == DRAFT_AWARE_INTERVIEW_WORKFLOW_VERSION
                        and persisted_interview_plan_artifact is not None
                        and reuse_action_selected
                    ):
                        raise DraftRevisionNotAllowed(
                            "v9 parent already has a supplemental interview plan; "
                            "unused-material recovery cannot bypass the answered-question path"
                        )
                    if (
                        parent.workflow_version == DRAFT_AWARE_INTERVIEW_WORKFLOW_VERSION
                        and supplemental_action_selected
                        and (
                            persisted_interview_plan_artifact is not None
                            or interview_provenance_present
                        )
                    ):
                        if (
                            request.supplemental_interview_plan_artifact_id is None
                            or not request.answered_question_ids
                        ):
                            raise DraftRevisionNotAllowed(
                                "v9 supplemental material requires one persisted interview "
                                "plan and answered question IDs"
                            )
                        if parent_supplemental_round >= MAX_SUPPLEMENTAL_INTERVIEW_ROUNDS:
                            raise DraftRevisionNotAllowed(
                                "supplemental interview round limit has been reached"
                            )
                        interview_plan_artifact = await session.get(
                            Artifact,
                            request.supplemental_interview_plan_artifact_id,
                        )
                        if (
                            interview_plan_artifact is None
                            or persisted_interview_plan_artifact is None
                            or interview_plan_artifact.id != persisted_interview_plan_artifact.id
                            or interview_plan_artifact.run_id != parent.id
                            or interview_plan_artifact.kind
                            != f"{PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW}_result"
                            or interview_plan_artifact.task_id is None
                        ):
                            raise DraftRevisionNotAllowed(
                                "supplemental interview plan is unavailable for this parent Run"
                            )
                        interview_plan_task = await session.get(
                            Task,
                            interview_plan_artifact.task_id,
                        )
                        try:
                            interview_plan = SupplementalInterviewPlan.model_validate(
                                {
                                    key: value
                                    for key, value in interview_plan_artifact.content_json.items()
                                    if key != "_execution"
                                }
                            )
                        except ValidationError as error:
                            raise DraftRevisionNotAllowed(
                                "supplemental interview plan is invalid"
                            ) from error
                        interview_task_status_is_valid = interview_plan_task is not None and (
                            interview_plan_task.status == TaskStatus.SUCCEEDED
                            or (
                                interview_plan_task.status == TaskStatus.FAILED
                                and interview_plan.generation_mode == "deterministic_fallback"
                            )
                        )
                        if (
                            interview_plan_task is None
                            or interview_plan_task.run_id != parent.id
                            or interview_plan_task.kind != PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW
                            or not interview_task_status_is_valid
                            or interview_plan_task.output_artifact_id != interview_plan_artifact.id
                        ):
                            raise DraftRevisionNotAllowed(
                                "supplemental interview plan provenance is unavailable"
                            )
                        expected_round = parent_supplemental_round + 1
                        if (
                            interview_plan.draft_artifact_id != parent_draft.id
                            or interview_plan.quality_report_artifact_id != parent_report.id
                            or interview_plan.round_number != expected_round
                            or interview_plan.round_number > MAX_SUPPLEMENTAL_INTERVIEW_ROUNDS
                        ):
                            raise DraftRevisionNotAllowed(
                                "supplemental interview plan is stale or targets another Draft"
                            )
                        available_question_ids = {
                            question.question_id for question in interview_plan.questions
                        }
                        unknown_question_ids = [
                            question_id
                            for question_id in request.answered_question_ids
                            if question_id not in available_question_ids
                        ]
                        if unknown_question_ids:
                            raise DraftRevisionNotAllowed(
                                "answered supplemental interview question is unavailable: "
                                f"{unknown_question_ids[0]}"
                            )
                        child_supplemental_round = interview_plan.round_number
                    elif interview_provenance_present:
                        raise DraftRevisionNotAllowed(
                            "supplemental interview provenance is not available for this Run"
                        )

                    selected_gaps = {gap.code: gap for gap in plan_record.plan.gaps}
                    missing_gap_codes = [
                        code for code in request.selected_gap_codes if code not in selected_gaps
                    ]
                    if missing_gap_codes:
                        raise DraftRevisionNotAllowed(
                            f"selected improvement gap is unavailable: {missing_gap_codes[0]}"
                        )
                    reuse_requested = "reuse_unused_material" in request.selected_actions
                    reuse_option_available = any(
                        option.kind == "reuse_unused_material"
                        for option in plan_record.plan.options
                    )
                    minimum_characters, _maximum_characters = duration_character_bounds(
                        plan_record.plan.duration.target_script_character_count
                    )
                    parent_is_below_duration_minimum = (
                        plan_record.plan.duration.actual_script_character_count < minimum_characters
                    )
                    if reuse_requested and (
                        not reuse_option_available or not parent_is_below_duration_minimum
                    ):
                        raise DraftRevisionNotAllowed(
                            "improvement plan does not offer unused factual material "
                            "for length recovery"
                        )

                    feedback_artifacts = (
                        (
                            await session.execute(
                                select(Artifact).where(
                                    Artifact.id.in_(request.selected_feedback_artifact_ids)
                                )
                            )
                        )
                        .scalars()
                        .all()
                        if request.selected_feedback_artifact_ids
                        else []
                    )
                    feedback_by_id = {artifact.id: artifact for artifact in feedback_artifacts}
                    selected_feedback: list[dict[str, Any]] = []
                    for artifact_id in request.selected_feedback_artifact_ids:
                        artifact = feedback_by_id.get(artifact_id)
                        if (
                            artifact is None
                            or artifact.run_id != parent.id
                            or artifact.kind != "draft_user_feedback"
                        ):
                            raise DraftRevisionNotAllowed(
                                f"selected feedback is unavailable: {artifact_id}"
                            )
                        try:
                            feedback = DraftUserFeedback.model_validate(artifact.content_json)
                        except ValidationError as error:
                            raise DraftRevisionNotAllowed(
                                "selected feedback artifact is invalid"
                            ) from error
                        selected_feedback.append(
                            {
                                "artifact_id": artifact.id,
                                "feedback_origin": feedback.feedback_origin,
                                "decision": feedback.decision,
                                "overall_rating": feedback.overall_rating,
                                "voice_match_rating": feedback.voice_match_rating,
                                "recordability_rating": feedback.recordability_rating,
                                "usefulness_rating": feedback.usefulness_rating,
                                "tone_fit_rating": feedback.tone_fit_rating,
                                "would_record_as_is": feedback.would_record_as_is,
                                "observed_duration_minutes": (feedback.observed_duration_minutes),
                                "comment": feedback.comment,
                            }
                        )

                    base_editor_input = _base_editor_input(parent_editor_task.input_json)
                    parent_brief = dict(base_editor_input["creative_brief"])
                    if request.target_duration_minutes is not None:
                        if request.target_duration_minutes >= int(
                            parent_brief["target_duration_minutes"]
                        ):
                            raise DraftRevisionNotAllowed(
                                "revised target duration must be lower than the parent target"
                            )
                        allowed_lower_targets = {
                            option.suggested_target_duration_minutes
                            for option in plan_record.plan.options
                            if option.kind == "lower_target_duration"
                        }
                        if request.target_duration_minutes not in allowed_lower_targets:
                            raise DraftRevisionNotAllowed(
                                "requested target duration is not offered by the improvement plan"
                            )
                        parent_brief["target_duration_minutes"] = request.target_duration_minutes

                    parent_factual_source_ids = _stable_unique(
                        [
                            str(segment["source_id"])
                            for segment in [
                                *base_editor_input["initial_source_segments"],
                                *base_editor_input["supplemental_source_segments"],
                            ]
                        ]
                    )
                    style_source_ids = {
                        str(segment["source_id"])
                        for segment in base_editor_input.get(
                            "writing_style_segments",
                            [],
                        )
                    }
                    duplicate_source_ids = [
                        source_id
                        for source_id in request.source_ids
                        if source_id in {*parent_factual_source_ids, *style_source_ids}
                    ]
                    if duplicate_source_ids:
                        raise DraftRevisionNotAllowed(
                            "supplemental revision Sources must be new factual material"
                        )
                    added_sources = (
                        (
                            await session.execute(
                                select(Source)
                                .where(Source.id.in_(request.source_ids))
                                .options(selectinload(Source.segments))
                            )
                        )
                        .scalars()
                        .all()
                        if request.source_ids
                        else []
                    )
                    added_sources_by_id = {source.id: source for source in added_sources}
                    missing_source_ids = [
                        source_id
                        for source_id in request.source_ids
                        if source_id not in added_sources_by_id
                    ]
                    if missing_source_ids:
                        raise RunSourceNotFound(missing_source_ids[0])
                    if parent.project_id is not None and request.source_ids:
                        linked_source_ids = set(
                            (
                                await session.execute(
                                    select(ProjectSource.source_id).where(
                                        ProjectSource.project_id == parent.project_id,
                                        ProjectSource.source_id.in_(request.source_ids),
                                    )
                                )
                            ).scalars()
                        )
                        unlinked_source_ids = [
                            source_id
                            for source_id in request.source_ids
                            if source_id not in linked_source_ids
                        ]
                        if unlinked_source_ids:
                            raise ProjectSourceNotLinked(unlinked_source_ids[0])
                    added_segments = _segments_for_sources(
                        request.source_ids,
                        added_sources_by_id,
                    )
                    supplemental_segments = [
                        *base_editor_input["supplemental_source_segments"],
                        *added_segments,
                    ]
                    if len(supplemental_segments) > MAX_EDITOR_SUPPLEMENTAL_SEGMENTS:
                        raise DraftRevisionNotAllowed(
                            "revision material exceeds the 500 segment MVP limit"
                        )

                    child = Run(
                        project_id=parent.project_id,
                        parent_run_id=parent.id,
                        workflow_type="podcast-revision",
                        workflow_version=(
                            GUIDED_REVISION_WORKFLOW_VERSION
                            if request.version == LEGACY_DRAFT_REVISION_REQUEST_VERSION
                            else DRAFT_AWARE_INTERVIEW_WORKFLOW_VERSION
                        ),
                        status=RunStatus.QUEUED,
                        current_step=REVISE_PODCAST_DRAFT,
                        input_json={
                            "topic": parent.input_json["topic"],
                            "source_ids": [
                                *parent_factual_source_ids,
                                *request.source_ids,
                            ],
                            "creative_brief": parent_brief,
                            "draft_quality": parent.input_json["draft_quality"],
                            "parent_run_id": parent.id,
                            "parent_draft_artifact_id": parent_draft.id,
                            "parent_quality_report_artifact_id": parent_report.id,
                            "plan_artifact_id": plan_record.artifact.id,
                            "supplemental_interview_round": child_supplemental_round,
                            **(
                                {
                                    "writing_style_profile": base_editor_input[
                                        "writing_style_profile"
                                    ]
                                }
                                if base_editor_input.get("writing_style_profile") is not None
                                else {}
                            ),
                        },
                    )
                    session.add(child)
                    await session.flush()

                    request_record = DraftRevisionRequestRecord(
                        version=request.version,
                        submission_id=request.submission_id,
                        parent_run_id=parent.id,
                        child_run_id=child.id,
                        plan_artifact_id=plan_record.artifact.id,
                        parent_draft_artifact_id=parent_draft.id,
                        parent_quality_report_artifact_id=parent_report.id,
                        selected_actions=request.selected_actions,
                        selected_feedback_artifact_ids=(request.selected_feedback_artifact_ids),
                        selected_gap_codes=request.selected_gap_codes,
                        source_ids=request.source_ids,
                        supplemental_interview_plan_artifact_id=(
                            request.supplemental_interview_plan_artifact_id
                        ),
                        answered_question_ids=request.answered_question_ids,
                        target_duration_minutes=request.target_duration_minutes,
                        revision_instruction=request.revision_instruction,
                    )
                    request_artifact = Artifact(
                        run_id=parent.id,
                        task_id=None,
                        kind="draft_revision_request",
                        content_json=request_record.model_dump(mode="json"),
                        idempotency_key=idempotency_key,
                    )
                    session.add(request_artifact)
                    await session.flush()
                    request_artifact_id = request_artifact.id

                    length_recovery_plan = (
                        build_draft_length_recovery_plan(
                            improvement_plan=plan_record.plan,
                            target_duration_minutes=int(parent_brief["target_duration_minutes"]),
                        )
                        if "reuse_unused_material" in request.selected_actions
                        else None
                    )
                    revision_input = {
                        "task_kind": REVISE_PODCAST_DRAFT,
                        "topic": parent.input_json["topic"],
                        "parent_run_id": parent.id,
                        "parent_draft_artifact_id": parent_draft.id,
                        "parent_quality_report_artifact_id": parent_report.id,
                        "plan_artifact_id": plan_record.artifact.id,
                        "request_artifact_id": request_artifact.id,
                        "supplemental_interview_round": child_supplemental_round,
                        "creative_brief": parent_brief,
                        "interview_scaffold": base_editor_input["interview_scaffold"],
                        "scaffold_artifact_id": base_editor_input["scaffold_artifact_id"],
                        "submission_artifact_id": base_editor_input["submission_artifact_id"],
                        "submission_artifact_ids": base_editor_input.get(
                            "submission_artifact_ids",
                            [],
                        ),
                        "parent_podcast_draft": {
                            key: value
                            for key, value in parent_draft.content_json.items()
                            if key != "_execution"
                        },
                        "initial_source_segments": base_editor_input["initial_source_segments"],
                        "supplemental_source_segments": supplemental_segments,
                        "selected_actions": request.selected_actions,
                        "added_source_ids": request.source_ids,
                        "selected_feedback": selected_feedback,
                        "selected_quality_gaps": [
                            selected_gaps[code].model_dump(mode="json")
                            for code in request.selected_gap_codes
                        ],
                        "revision_instruction": request.revision_instruction,
                        **(
                            {"length_recovery_plan": (length_recovery_plan.model_dump(mode="json"))}
                            if length_recovery_plan is not None
                            else {}
                        ),
                        **(
                            {
                                "writing_style_profile": base_editor_input["writing_style_profile"],
                                "writing_style_segments": base_editor_input[
                                    "writing_style_segments"
                                ],
                            }
                            if base_editor_input.get("writing_style_profile") is not None
                            else {}
                        ),
                    }
                    try:
                        parsed_revision_input = PodcastRevisionTaskInput.model_validate(
                            revision_input
                        )
                        revision_input = parsed_revision_input.model_dump(mode="json")
                        if parsed_revision_input.length_recovery_plan is None:
                            revision_input.pop("length_recovery_plan", None)
                    except (ValidationError, ValueError, TypeError) as error:
                        raise DraftRevisionNotAllowed(
                            "revision choices cannot build a valid Editor task"
                        ) from error
                    await append_event(
                        session,
                        run_id=parent.id,
                        event_type="workflow.draft_revision.requested",
                        payload={
                            "request_artifact_id": request_artifact.id,
                            "child_run_id": child.id,
                            "selected_action_count": len(request.selected_actions),
                            "selected_feedback_count": len(selected_feedback),
                            "selected_gap_count": len(request.selected_gap_codes),
                            "supplemental_source_count": len(request.source_ids),
                            "supplemental_interview_round": child_supplemental_round,
                            "answered_question_count": len(request.answered_question_ids),
                            **(
                                {
                                    "supplemental_interview_plan_artifact_id": (
                                        interview_plan_artifact.id
                                    )
                                }
                                if interview_plan_artifact is not None
                                else {}
                            ),
                            **(
                                {
                                    "length_recovery_readiness": (length_recovery_plan.readiness),
                                    "length_recovery_missing_to_minimum": (
                                        length_recovery_plan.missing_to_minimum_character_count
                                    ),
                                    "length_recovery_priority_source_count": len(
                                        length_recovery_plan.priority_unused_source_refs
                                    ),
                                }
                                if length_recovery_plan is not None
                                else {}
                            ),
                        },
                    )
                    await append_event(
                        session,
                        run_id=child.id,
                        event_type="run.created",
                        payload={
                            "workflow_type": child.workflow_type,
                            "workflow_version": child.workflow_version,
                            "parent_run_id": parent.id,
                            "request_artifact_id": request_artifact.id,
                        },
                    )
                    await self.orchestrator.start_revision_run(
                        session,
                        run=child,
                        input_json=revision_input,
                    )
                    child_run_id = child.id

        child_view = await self.get_run(child_run_id)
        logger.info(
            (
                "Revision request replay returned existing child Run"
                if idempotent_replay
                else "Revision child Run created"
            ),
            extra={
                "event": (
                    "workflow.draft_revision.idempotent_replay"
                    if idempotent_replay
                    else "workflow.draft_revision.requested"
                ),
                "run_id": parent_run_id,
                "child_run_id": child_run_id,
                "artifact_id": request_artifact_id,
                "idempotent_replay": idempotent_replay,
            },
        )
        return CreateDraftRevisionResponse(
            idempotent_replay=idempotent_replay,
            request_artifact_id=request_artifact_id,
            improvement_plan=plan_record,
            run=child_view,
        )

    async def get_draft_revision_comparison(
        self,
        run_id: str,
    ) -> DraftRevisionComparisonRecord:
        """Return or persist a text-free parent/child quality comparison."""

        async with self._run_mutation_lock:
            async with self.database.sessions() as session, session.begin():
                revision_run = await session.get(Run, run_id)
                if revision_run is None:
                    raise RunNotFound(run_id)
                if (
                    revision_run.workflow_type != "podcast-revision"
                    or revision_run.parent_run_id is None
                    or revision_run.status != RunStatus.SUCCEEDED
                    or revision_run.output_artifact_id is None
                ):
                    raise DraftRevisionComparisonNotReady(
                        "comparison requires a succeeded podcast-revision child Run"
                    )
                revision_task = (
                    await session.execute(
                        select(Task)
                        .where(
                            Task.run_id == revision_run.id,
                            Task.kind == REVISE_PODCAST_DRAFT,
                        )
                        .order_by(Task.created_at, Task.id)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if revision_task is None:
                    raise DraftRevisionComparisonNotReady("revision task provenance is unavailable")
                try:
                    task_input = PodcastRevisionTaskInput.model_validate(revision_task.input_json)
                except ValidationError as error:
                    raise DraftRevisionComparisonNotReady(
                        "persisted revision task is invalid"
                    ) from error
                if task_input.parent_run_id != revision_run.parent_run_id:
                    raise DraftRevisionComparisonNotReady(
                        "revision Run lineage differs from its task provenance"
                    )

                parent_run = await session.get(Run, task_input.parent_run_id)
                parent_draft = await session.get(
                    Artifact,
                    task_input.parent_draft_artifact_id,
                )
                revision_draft = await session.get(
                    Artifact,
                    revision_run.output_artifact_id,
                )
                parent_report = await session.get(
                    Artifact,
                    task_input.parent_quality_report_artifact_id,
                )
                revision_report = (
                    await session.execute(
                        select(Artifact)
                        .where(
                            Artifact.run_id == revision_run.id,
                            Artifact.kind == "draft_quality_report",
                        )
                        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if (
                    parent_run is None
                    or parent_run.id != revision_run.parent_run_id
                    or parent_draft is None
                    or parent_draft.run_id != parent_run.id
                    or parent_report is None
                    or parent_report.run_id != parent_run.id
                    or parent_report.kind != "draft_quality_report"
                    or revision_draft is None
                    or revision_draft.run_id != revision_run.id
                    or revision_draft.kind != f"{REVISE_PODCAST_DRAFT}_result"
                    or revision_report is None
                    or revision_report.kind != "draft_quality_report"
                ):
                    raise DraftRevisionComparisonNotReady(
                        "parent or revision quality provenance is unavailable"
                    )

                comparison_key = (
                    f"draft-revision-comparison:{parent_draft.id}:{revision_draft.id}:"
                    f"{parent_report.id}:{revision_report.id}:"
                    f"{DraftRevisionComparison.model_fields['version'].default}"
                )
                comparison_artifact = (
                    await session.execute(
                        select(Artifact).where(Artifact.idempotency_key == comparison_key)
                    )
                ).scalar_one_or_none()
                if comparison_artifact is None:
                    try:
                        comparison = build_draft_revision_comparison(
                            parent=build_draft_revision_candidate_summary(
                                run_id=parent_run.id,
                                draft_artifact_id=parent_draft.id,
                                quality_report_artifact_id=parent_report.id,
                                quality_report=parent_report.content_json,
                            ),
                            revision=build_draft_revision_candidate_summary(
                                run_id=revision_run.id,
                                draft_artifact_id=revision_draft.id,
                                quality_report_artifact_id=revision_report.id,
                                quality_report=revision_report.content_json,
                            ),
                        )
                    except (ValidationError, ValueError, TypeError) as error:
                        raise DraftRevisionComparisonNotReady(
                            "quality artifacts cannot produce a valid comparison"
                        ) from error
                    comparison_artifact = Artifact(
                        run_id=revision_run.id,
                        task_id=revision_task.id,
                        kind="draft_revision_comparison",
                        content_json=comparison.model_dump(mode="json"),
                        idempotency_key=comparison_key,
                    )
                    session.add(comparison_artifact)
                    await session.flush()
                    await append_event(
                        session,
                        run_id=revision_run.id,
                        task_id=revision_task.id,
                        event_type="workflow.draft_revision.compared",
                        payload={
                            "artifact_id": comparison_artifact.id,
                            "parent_run_id": parent_run.id,
                            "parent_draft_artifact_id": parent_draft.id,
                            "revision_draft_artifact_id": revision_draft.id,
                            "script_character_delta": comparison.script_character_delta,
                            "estimated_duration_delta_minutes": (
                                comparison.estimated_duration_delta_minutes
                            ),
                            "deterministic_score_delta": (comparison.deterministic_score_delta),
                            "automatic_winner_selected": False,
                        },
                    )
                try:
                    comparison = DraftRevisionComparison.model_validate(
                        comparison_artifact.content_json
                    )
                except ValidationError as error:
                    raise DraftRevisionComparisonNotReady(
                        "persisted revision comparison is invalid"
                    ) from error
                artifact_view = ArtifactView.model_validate(comparison_artifact)

        logger.info(
            "Draft revision comparison ready",
            extra={
                "event": "workflow.draft_revision.compared",
                "run_id": run_id,
                "artifact_id": artifact_view.id,
                "parent_run_id": comparison.parent.run_id,
                "script_character_delta": comparison.script_character_delta,
                "estimated_duration_delta_minutes": (comparison.estimated_duration_delta_minutes),
                "deterministic_score_delta": comparison.deterministic_score_delta,
            },
        )
        return DraftRevisionComparisonRecord(
            comparison=comparison,
            artifact=artifact_view,
        )

    async def export_draft_quality_markdown(self, run_id: str) -> str:
        record = await self.get_draft_quality_report(run_id)
        try:
            markdown = render_draft_quality_markdown(record.report.model_dump(mode="json"))
        except (ValidationError, ValueError, TypeError) as error:
            raise DraftQualityReportNotReady("draft quality report cannot be exported") from error
        logger.info(
            "Draft quality Markdown exported",
            extra={
                "event": "run.draft_quality_markdown.exported",
                "run_id": run_id,
                "artifact_id": record.artifact.id,
                "quality_report_id": record.artifact.id,
                "quality_decision": record.report.decision,
                "markdown_char_count": len(markdown),
            },
        )
        return markdown

    async def export_interview_scaffold_markdown(self, run_id: str) -> str:
        async with self.database.sessions() as session:
            run = await session.get(Run, run_id)
            if run is None:
                raise RunNotFound(run_id)
            artifact = (
                await session.execute(
                    select(Artifact)
                    .where(
                        Artifact.run_id == run.id,
                        Artifact.kind == "build_interview_scaffold_result",
                    )
                    .order_by(Artifact.created_at.desc(), Artifact.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if artifact is None:
                raise InterviewScaffoldExportNotReady("interview scaffold is not ready for export")

            # Worker metadata belongs to runtime tracing, not the strict product
            # artifact rendered for the user.
            content = {
                key: value for key, value in artifact.content_json.items() if key != "_execution"
            }
            try:
                reference_keys = interview_scaffold_reference_keys(content)
            except (ValueError, TypeError) as error:
                raise InterviewScaffoldExportNotReady(
                    "interview scaffold output is invalid"
                ) from error

            referenced_source_ids = sorted({source_id for source_id, _ in reference_keys})
            sources = (
                (
                    await session.execute(
                        select(Source)
                        .where(Source.id.in_(referenced_source_ids))
                        .options(selectinload(Source.segments))
                    )
                )
                .scalars()
                .all()
            )
            required_keys = set(reference_keys)
            source_citations = {
                (source.id, segment.id): SourceCitation(
                    title=source.title,
                    segment_position=segment.position,
                )
                for source in sources
                for segment in source.segments
                if (source.id, segment.id) in required_keys
            }
            try:
                markdown = render_interview_scaffold_markdown(
                    content,
                    source_citations=source_citations,
                )
            except (ValueError, TypeError) as error:
                raise InterviewScaffoldExportNotReady(
                    "interview scaffold source metadata is unavailable"
                ) from error

        logger.info(
            "Interview scaffold Markdown exported",
            extra={
                "event": "run.interview_scaffold_markdown.exported",
                "run_id": run_id,
                "artifact_id": artifact.id,
                "markdown_char_count": len(markdown),
                "source_citation_count": len(reference_keys),
            },
        )
        return markdown

    async def export_podcast_draft_markdown(self, run_id: str) -> str:
        return await self._export_editor_markdown(run_id, export_kind="podcast_draft")

    async def export_show_notes_markdown(self, run_id: str) -> str:
        return await self._export_editor_markdown(run_id, export_kind="show_notes")

    async def _export_editor_markdown(
        self,
        run_id: str,
        *,
        export_kind: str,
    ) -> str:
        async with self.database.sessions() as session:
            run = await session.get(Run, run_id)
            if run is None:
                raise RunNotFound(run_id)
            if run.status != RunStatus.SUCCEEDED or run.output_artifact_id is None:
                raise PodcastDraftExportNotReady("podcast draft is not ready for export")

            artifact = await session.get(Artifact, run.output_artifact_id)
            if (
                artifact is None
                or artifact.run_id != run.id
                or artifact.kind
                not in {
                    f"{BUILD_PODCAST_DRAFT}_result",
                    f"{REVISE_PODCAST_DRAFT}_result",
                }
            ):
                raise PodcastDraftExportNotReady("run output is not a podcast draft")

            content = {
                key: value for key, value in artifact.content_json.items() if key != "_execution"
            }
            try:
                reference_keys = editor_output_reference_keys(content)
            except (ValidationError, ValueError, TypeError) as error:
                raise PodcastDraftExportNotReady("podcast draft output is invalid") from error

            referenced_source_ids = sorted({source_id for source_id, _ in reference_keys})
            sources = (
                (
                    await session.execute(
                        select(Source)
                        .where(Source.id.in_(referenced_source_ids))
                        .options(selectinload(Source.segments))
                    )
                )
                .scalars()
                .all()
            )
            required_keys = set(reference_keys)
            source_citations = {
                (source.id, segment.id): SourceCitation(
                    title=source.title,
                    segment_position=segment.position,
                )
                for source in sources
                for segment in source.segments
                if (source.id, segment.id) in required_keys
            }
            try:
                if export_kind == "podcast_draft":
                    markdown = render_podcast_draft_markdown(
                        content,
                        source_citations=source_citations,
                    )
                elif export_kind == "show_notes":
                    markdown = render_show_notes_markdown(
                        content,
                        source_citations=source_citations,
                    )
                else:
                    raise ValueError(f"unsupported Editor export kind: {export_kind}")
            except (ValidationError, ValueError, TypeError) as error:
                raise PodcastDraftExportNotReady(
                    "podcast draft source metadata is unavailable"
                ) from error

        logger.info(
            "Editor Markdown exported",
            extra={
                "event": f"run.{export_kind}_markdown.exported",
                "run_id": run_id,
                "artifact_id": artifact.id,
                "markdown_char_count": len(markdown),
                "source_citation_count": len(reference_keys),
            },
        )
        return markdown

    async def resume_run(
        self,
        run_id: str,
        *,
        checkpoint: str,
        submission_id: str,
        source_ids: list[str],
    ) -> ResumeRunResponse:
        async with self.database.sessions() as version_session:
            versioned_run = await version_session.get(Run, run_id)
            if versioned_run is None:
                raise RunNotFound(run_id)
            if versioned_run.workflow_version in {
                MATERIAL_READINESS_WORKFLOW_VERSION,
                *QUALITY_REVIEW_WORKFLOW_VERSIONS,
            }:
                return await self._resume_material_readiness_run(
                    run_id,
                    checkpoint=checkpoint,
                    submission_id=submission_id,
                    source_ids=source_ids,
                )

        async with self._run_mutation_lock:
            resumed = False
            idempotent_replay = False
            source_count = len(source_ids)
            segment_count = 0
            submission_artifact_id: str
            idempotency_key = stable_id(
                "human_input",
                "\x00".join([run_id, checkpoint, submission_id]),
            )

            async with self.database.sessions() as session, session.begin():
                run = await session.get(Run, run_id)
                if run is None:
                    raise RunNotFound(run_id)

                existing = (
                    await session.execute(
                        select(Artifact).where(
                            Artifact.run_id == run.id,
                            Artifact.idempotency_key == idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    existing_content = existing.content_json
                    if (
                        existing.kind != "user_material_submission"
                        or existing_content.get("checkpoint") != checkpoint
                        or existing_content.get("submission_id") != submission_id
                        or existing_content.get("source_ids") != source_ids
                    ):
                        logger.warning(
                            "Resume submission conflicted with an existing idempotency key",
                            extra={
                                "event": "run.resume.rejected",
                                "run_id": run.id,
                                "checkpoint": checkpoint,
                                "source_count": source_count,
                                "error_code": "resume_submission_conflict",
                            },
                        )
                        raise RunResumeConflict(
                            "submission_id was already used with different material"
                        )
                    submission_artifact_id = existing.id
                    segment_count = len(existing_content.get("source_refs", []))
                    idempotent_replay = True
                else:
                    if (
                        run.workflow_type != "episode-research"
                        or run.workflow_version
                        not in {
                            INTERVIEW_RESEARCH_WORKFLOW_VERSION,
                            EDITOR_RESEARCH_WORKFLOW_VERSION,
                        }
                        or run.status != RunStatus.WAITING_FOR_USER
                        or run.current_step != "awaiting_interview_response"
                    ):
                        logger.warning(
                            "Run rejected Resume outside the interview checkpoint",
                            extra={
                                "event": "run.resume.rejected",
                                "run_id": run.id,
                                "checkpoint": checkpoint,
                                "source_count": source_count,
                                "status": run.status,
                                "error_code": "run_resume_not_allowed",
                            },
                        )
                        raise RunResumeNotAllowed(
                            "run is not waiting for interview scaffold material"
                        )
                    if checkpoint != INTERVIEW_SCAFFOLD_CHECKPOINT:
                        raise RunResumeNotAllowed("run is not waiting at this checkpoint")

                    scaffold = (
                        await session.get(Artifact, run.output_artifact_id)
                        if run.output_artifact_id is not None
                        else None
                    )
                    if (
                        scaffold is None
                        or scaffold.run_id != run.id
                        or scaffold.kind != "build_interview_scaffold_result"
                    ):
                        raise RunResumeNotAllowed(
                            "run does not have a valid interview scaffold checkpoint"
                        )

                    scaffold_content = {
                        key: value
                        for key, value in scaffold.content_json.items()
                        if key != "_execution"
                    }
                    try:
                        scaffold_reference_keys = interview_scaffold_reference_keys(
                            scaffold_content
                        )
                    except (ValueError, TypeError) as error:
                        raise RunResumeNotAllowed(
                            "run does not have a valid interview scaffold checkpoint"
                        ) from error

                    source_ids_to_load = sorted(
                        {
                            *source_ids,
                            *(source_id for source_id, _ in scaffold_reference_keys),
                        }
                    )
                    sources = (
                        (
                            await session.execute(
                                select(Source)
                                .where(Source.id.in_(source_ids_to_load))
                                .options(selectinload(Source.segments))
                            )
                        )
                        .scalars()
                        .all()
                    )
                    sources_by_id = {source.id: source for source in sources}
                    missing_source_ids = [
                        source_id for source_id in source_ids if source_id not in sources_by_id
                    ]
                    if missing_source_ids:
                        raise RunSourceNotFound(missing_source_ids[0])

                    source_refs = [
                        {
                            "source_id": source.id,
                            "source_segment_id": segment.id,
                        }
                        for source_id in source_ids
                        for source in [sources_by_id[source_id]]
                        for segment in sorted(source.segments, key=lambda item: item.position)
                    ]
                    segment_count = len(source_refs)
                    editor_input_json: dict[str, Any] | None = None
                    submission_artifact_id = new_id("art")

                    if run.workflow_version == EDITOR_RESEARCH_WORKFLOW_VERSION:
                        segments_by_key = {
                            (source.id, segment.id): segment
                            for source in sources
                            for segment in source.segments
                        }
                        missing_scaffold_references = [
                            key for key in scaffold_reference_keys if key not in segments_by_key
                        ]
                        if missing_scaffold_references:
                            raise RunResumeNotAllowed(
                                "interview scaffold source material is unavailable"
                            )

                        initial_source_segments = [
                            {
                                "source_id": source_id,
                                "source_segment_id": segment_id,
                                "text": segments_by_key[(source_id, segment_id)].text,
                            }
                            for source_id, segment_id in scaffold_reference_keys
                        ]
                        supplemental_source_segments = [
                            {
                                "source_id": source.id,
                                "source_segment_id": segment.id,
                                "text": segment.text,
                            }
                            for source_id in source_ids
                            for source in [sources_by_id[source_id]]
                            for segment in sorted(
                                source.segments,
                                key=lambda item: item.position,
                            )
                        ]
                        try:
                            editor_input_json = PodcastDraftTaskInput.model_validate(
                                {
                                    "task_kind": BUILD_PODCAST_DRAFT,
                                    "topic": run.input_json["topic"],
                                    "scaffold_artifact_id": scaffold.id,
                                    "submission_artifact_id": submission_artifact_id,
                                    "interview_scaffold": scaffold_content,
                                    "initial_source_segments": initial_source_segments,
                                    "supplemental_source_segments": (supplemental_source_segments),
                                }
                            ).model_dump(mode="json")
                        except (ValidationError, ValueError, TypeError) as error:
                            logger.warning(
                                "Resume material could not build a valid Editor task",
                                extra={
                                    "event": "run.resume.rejected",
                                    "run_id": run.id,
                                    "checkpoint": checkpoint,
                                    "source_count": source_count,
                                    "segment_count": segment_count,
                                    "error_code": "invalid_editor_task_input",
                                },
                            )
                            raise RunResumeNotAllowed(
                                "submitted material cannot build a valid Editor task"
                            ) from error

                    submission = Artifact(
                        id=submission_artifact_id,
                        run_id=run.id,
                        task_id=None,
                        kind="user_material_submission",
                        content_json={
                            "checkpoint": checkpoint,
                            "submission_id": submission_id,
                            "scaffold_artifact_id": scaffold.id,
                            "source_ids": source_ids,
                            "source_refs": source_refs,
                        },
                        idempotency_key=idempotency_key,
                    )
                    session.add(submission)
                    await session.flush()

                    validate_run_transition(run.status, RunStatus.RUNNING)
                    run.status = RunStatus.RUNNING
                    run.current_step = "accepting_user_material"
                    await append_event(
                        session,
                        run_id=run.id,
                        event_type="run.resumed",
                        payload={
                            "checkpoint": checkpoint,
                            "submission_artifact_id": submission.id,
                        },
                    )
                    await append_event(
                        session,
                        run_id=run.id,
                        event_type="workflow.user_material.accepted",
                        payload={
                            "checkpoint": checkpoint,
                            "submission_artifact_id": submission.id,
                            "source_count": source_count,
                            "segment_count": segment_count,
                        },
                    )

                    if run.workflow_version == EDITOR_RESEARCH_WORKFLOW_VERSION:
                        if editor_input_json is None:
                            raise RuntimeError("validated Editor input is missing")
                        await self.orchestrator.enqueue_editor(
                            session,
                            run=run,
                            input_json=editor_input_json,
                        )
                    else:
                        # Preserve the historical v3 meaning for Runs that were
                        # already persisted before M3.2 introduced the Editor.
                        validate_run_transition(run.status, RunStatus.SUCCEEDED)
                        run.status = RunStatus.SUCCEEDED
                        run.current_step = "complete"
                        await append_event(
                            session,
                            run_id=run.id,
                            event_type="run.succeeded",
                            payload={
                                "output_artifact_id": scaffold.id,
                                "checkpoint": checkpoint,
                            },
                        )
                    resumed = True

            run_view = await self.get_run(run_id)
            logger.info(
                (
                    "Resume replay returned the existing user material"
                    if idempotent_replay
                    else "Run accepted user material and resumed"
                ),
                extra={
                    "event": (
                        "run.resume.idempotent_replay"
                        if idempotent_replay
                        else "run.resume.accepted"
                    ),
                    "run_id": run_id,
                    "artifact_id": submission_artifact_id,
                    "checkpoint": checkpoint,
                    "source_count": source_count,
                    "segment_count": segment_count,
                    "idempotent_replay": idempotent_replay,
                },
            )
            return ResumeRunResponse(
                resumed=resumed,
                idempotent_replay=idempotent_replay,
                submission_artifact_id=submission_artifact_id,
                run=run_view,
            )

    async def _resume_material_readiness_run(
        self,
        run_id: str,
        *,
        checkpoint: str,
        submission_id: str,
        source_ids: list[str],
    ) -> ResumeRunResponse:
        """Accept one v5 material round, reassess, and queue Editor only when ready."""

        async with self._run_mutation_lock:
            resumed = False
            idempotent_replay = False
            editor_queued = False
            readiness_status: str | None = None
            source_count = len(source_ids)
            segment_count = 0
            submission_artifact_id: str
            idempotency_key = stable_id(
                "human_input",
                "\x00".join([run_id, checkpoint, submission_id]),
            )

            async with self.database.sessions() as session, session.begin():
                run = await session.get(Run, run_id)
                if run is None:
                    raise RunNotFound(run_id)

                existing = (
                    await session.execute(
                        select(Artifact).where(
                            Artifact.run_id == run.id,
                            Artifact.idempotency_key == idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    existing_content = existing.content_json
                    if (
                        existing.kind != "user_material_submission"
                        or existing_content.get("checkpoint") != checkpoint
                        or existing_content.get("submission_id") != submission_id
                        or existing_content.get("source_ids") != source_ids
                    ):
                        raise RunResumeConflict(
                            "submission_id was already used with different material"
                        )
                    submission_artifact_id = existing.id
                    segment_count = len(existing_content.get("source_refs", []))
                    idempotent_replay = True
                else:
                    if (
                        run.workflow_type != "episode-research"
                        or run.workflow_version
                        not in {
                            MATERIAL_READINESS_WORKFLOW_VERSION,
                            *QUALITY_REVIEW_WORKFLOW_VERSIONS,
                        }
                        or run.status != RunStatus.WAITING_FOR_USER
                        or run.current_step != "awaiting_more_material"
                    ):
                        raise RunResumeNotAllowed("run is not waiting for supplemental material")
                    if checkpoint != MATERIAL_READINESS_CHECKPOINT:
                        raise RunResumeNotAllowed("run is not waiting at this checkpoint")

                    scaffold = (
                        await session.execute(
                            select(Artifact)
                            .where(
                                Artifact.run_id == run.id,
                                Artifact.kind == f"{BUILD_INTERVIEW_SCAFFOLD}_result",
                            )
                            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if scaffold is None:
                        raise RunResumeNotAllowed(
                            "run does not have a valid interview scaffold checkpoint"
                        )
                    scaffold_content = {
                        key: value
                        for key, value in scaffold.content_json.items()
                        if key != "_execution"
                    }
                    try:
                        parsed_scaffold = InterviewScaffoldOutput.model_validate(scaffold_content)
                        scaffold_reference_keys = interview_scaffold_reference_keys(
                            scaffold_content
                        )
                    except (ValidationError, ValueError, TypeError) as error:
                        raise RunResumeNotAllowed(
                            "run does not have a valid interview scaffold checkpoint"
                        ) from error

                    prior_submissions = (
                        (
                            await session.execute(
                                select(Artifact)
                                .where(
                                    Artifact.run_id == run.id,
                                    Artifact.kind == "user_material_submission",
                                )
                                .order_by(Artifact.created_at, Artifact.id)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    initial_source_ids = list(run.input_json["source_ids"])
                    prior_supplemental_source_ids = _stable_unique(
                        [
                            source_id
                            for artifact in prior_submissions
                            for source_id in artifact.content_json.get("source_ids", [])
                        ]
                    )
                    already_used_source_ids = {
                        *initial_source_ids,
                        *prior_supplemental_source_ids,
                    }
                    repeated_source_ids = [
                        source_id
                        for source_id in source_ids
                        if source_id in already_used_source_ids
                    ]
                    if repeated_source_ids:
                        raise RunResumeNotAllowed(
                            "submitted material must add Sources not already used by this Run"
                        )
                    supplemental_source_ids = _stable_unique(
                        [*prior_supplemental_source_ids, *source_ids]
                    )
                    all_source_ids = _stable_unique([*initial_source_ids, *supplemental_source_ids])
                    sources = (
                        (
                            await session.execute(
                                select(Source)
                                .where(Source.id.in_(all_source_ids))
                                .options(selectinload(Source.segments))
                            )
                        )
                        .scalars()
                        .all()
                    )
                    sources_by_id = {source.id: source for source in sources}
                    missing_source_ids = [
                        source_id for source_id in source_ids if source_id not in sources_by_id
                    ]
                    if missing_source_ids:
                        raise RunSourceNotFound(missing_source_ids[0])

                    segments_by_key = {
                        (source.id, segment.id): segment
                        for source in sources
                        for segment in source.segments
                    }
                    missing_scaffold_references = [
                        key for key in scaffold_reference_keys if key not in segments_by_key
                    ]
                    if missing_scaffold_references:
                        raise RunResumeNotAllowed(
                            "interview scaffold source material is unavailable"
                        )
                    initial_segments = [
                        {
                            "source_id": source_id,
                            "source_segment_id": segment_id,
                            "text": segments_by_key[(source_id, segment_id)].text,
                        }
                        for source_id, segment_id in scaffold_reference_keys
                    ]
                    supplemental_segments = _segments_for_sources(
                        supplemental_source_ids,
                        sources_by_id,
                    )
                    if len(supplemental_segments) > MAX_EDITOR_SUPPLEMENTAL_SEGMENTS:
                        raise RunResumeNotAllowed(
                            "supplemental material exceeds the 500 segment MVP limit"
                        )

                    source_refs = [
                        {
                            "source_id": source.id,
                            "source_segment_id": segment.id,
                        }
                        for source_id in source_ids
                        for source in [sources_by_id[source_id]]
                        for segment in sorted(
                            source.segments,
                            key=lambda item: item.position,
                        )
                    ]
                    segment_count = len(source_refs)
                    submission_artifact_id = new_id("art")
                    follow_up_questions = [
                        ReadinessFollowUpQuestion(
                            prompt=question.prompt,
                            purpose=question.purpose,
                            source_refs=question.source_refs,
                        )
                        for section in parsed_scaffold.sections
                        for question in section.questions
                    ][:6]
                    try:
                        report = assess_material_readiness(
                            creative_brief=run.input_json["creative_brief"],
                            initial_source_segments=initial_segments,
                            supplemental_source_segments=supplemental_segments,
                            follow_up_questions=follow_up_questions,
                        )
                    except (ValidationError, ValueError, TypeError) as error:
                        raise RunResumeNotAllowed(
                            "submitted material cannot be evaluated safely"
                        ) from error
                    readiness_status = report.status

                    editor_input_json: dict[str, Any] | None = None
                    if report.status == "ready":
                        submission_artifact_ids = [
                            *[artifact.id for artifact in prior_submissions],
                            submission_artifact_id,
                        ]
                        writing_style_task_fields = await self._load_writing_style_task_fields(
                            session,
                            run=run,
                        )
                        try:
                            editor_input_json = PodcastDraftTaskInput.model_validate(
                                {
                                    "task_kind": BUILD_PODCAST_DRAFT,
                                    "topic": run.input_json["topic"],
                                    "scaffold_artifact_id": scaffold.id,
                                    "submission_artifact_id": submission_artifact_id,
                                    "submission_artifact_ids": (submission_artifact_ids),
                                    "creative_brief": run.input_json["creative_brief"],
                                    "interview_scaffold": scaffold_content,
                                    "initial_source_segments": initial_segments,
                                    "supplemental_source_segments": (supplemental_segments),
                                    **writing_style_task_fields,
                                }
                            ).model_dump(mode="json")
                        except (ValidationError, ValueError, TypeError) as error:
                            raise RunResumeNotAllowed(
                                "submitted material cannot build a valid Editor task"
                            ) from error

                    submission = Artifact(
                        id=submission_artifact_id,
                        run_id=run.id,
                        task_id=None,
                        kind="user_material_submission",
                        content_json={
                            "checkpoint": checkpoint,
                            "submission_id": submission_id,
                            "scaffold_artifact_id": scaffold.id,
                            "source_ids": source_ids,
                            "source_refs": source_refs,
                        },
                        idempotency_key=idempotency_key,
                    )
                    session.add(submission)
                    await session.flush()

                    validate_run_transition(run.status, RunStatus.RUNNING)
                    run.status = RunStatus.RUNNING
                    run.current_step = "assessing_material_readiness"
                    await append_event(
                        session,
                        run_id=run.id,
                        event_type="run.resumed",
                        payload={
                            "checkpoint": checkpoint,
                            "submission_artifact_id": submission.id,
                        },
                    )
                    await append_event(
                        session,
                        run_id=run.id,
                        event_type="workflow.user_material.accepted",
                        payload={
                            "checkpoint": checkpoint,
                            "submission_artifact_id": submission.id,
                            "source_count": source_count,
                            "segment_count": segment_count,
                        },
                    )
                    readiness_artifact = await self.orchestrator.persist_material_readiness(
                        session,
                        run=run,
                        report=report,
                        round_key=f"submission:{submission.id}",
                        task_id=None,
                    )

                    if report.status == "ready":
                        if editor_input_json is None:
                            raise RuntimeError("validated Editor input is missing")
                        await self.orchestrator.enqueue_editor(
                            session,
                            run=run,
                            input_json=editor_input_json,
                        )
                        editor_queued = True
                    else:
                        validate_run_transition(
                            run.status,
                            RunStatus.WAITING_FOR_USER,
                        )
                        run.status = RunStatus.WAITING_FOR_USER
                        run.current_step = "awaiting_more_material"
                        run.output_artifact_id = scaffold.id
                        checkpoint_payload = {
                            "checkpoint": MATERIAL_READINESS_CHECKPOINT,
                            "output_artifact_id": scaffold.id,
                            "readiness_artifact_id": readiness_artifact.id,
                            "readiness_status": report.status,
                            "additional_source_chars_needed": (
                                report.additional_source_chars_needed
                            ),
                        }
                        await append_event(
                            session,
                            run_id=run.id,
                            event_type="workflow.user_input.requested",
                            payload=checkpoint_payload,
                        )
                        await append_event(
                            session,
                            run_id=run.id,
                            event_type="run.waiting_for_user",
                            payload=checkpoint_payload,
                        )
                    resumed = True

            run_view = await self.get_run(run_id)
            logger.info(
                (
                    "Resume replay returned the existing user material"
                    if idempotent_replay
                    else "Run accepted supplemental material"
                ),
                extra={
                    "event": (
                        "run.resume.idempotent_replay"
                        if idempotent_replay
                        else "run.resume.accepted"
                    ),
                    "run_id": run_id,
                    "artifact_id": submission_artifact_id,
                    "checkpoint": checkpoint,
                    "source_count": source_count,
                    "segment_count": segment_count,
                    "idempotent_replay": idempotent_replay,
                    "readiness_status": readiness_status,
                    "editor_queued": editor_queued,
                },
            )
            return ResumeRunResponse(
                resumed=resumed,
                idempotent_replay=idempotent_replay,
                submission_artifact_id=submission_artifact_id,
                run=run_view,
            )

    async def cancel_run(self, run_id: str) -> RunView:
        async with self._run_mutation_lock:
            async with self.database.sessions() as session, session.begin():
                run = await session.get(Run, run_id)
                if run is None:
                    raise RunNotFound(run_id)
                if run.status in {
                    RunStatus.SUCCEEDED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
                    raise RunAlreadyTerminal(run.status)

                validate_run_transition(run.status, RunStatus.CANCELLED)
                run.status = RunStatus.CANCELLED
                run.cancel_requested_at = datetime.now(UTC)

                tasks = (
                    await session.execute(
                        select(Task).where(
                            Task.run_id == run_id,
                            Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
                        )
                    )
                ).scalars()
                for task in tasks:
                    validate_task_transition(task.status, TaskStatus.CANCELLED)
                    task.status = TaskStatus.CANCELLED
                    task.lease_token = None
                    task.lease_expires_at = None
                    await append_event(
                        session,
                        run_id=run.id,
                        task_id=task.id,
                        event_type="task.cancelled",
                        payload={"kind": task.kind},
                    )

                await append_event(
                    session,
                    run_id=run.id,
                    event_type="run.cancelled",
                    payload={},
                )

            logger.info(
                "Run cancelled",
                extra={
                    "event": "run.cancelled",
                    "run_id": run_id,
                },
            )
            return await self.get_run(run_id)


def _stable_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _project_run_request_fingerprint(
    *,
    workflow_type: str,
    payload: dict[str, object],
) -> str:
    serialized = json.dumps(
        {"workflow_type": workflow_type, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _segments_for_sources(
    source_ids: list[str],
    sources_by_id: dict[str, Source],
) -> list[dict[str, str]]:
    return [
        {
            "source_id": source.id,
            "source_segment_id": segment.id,
            "text": segment.text,
        }
        for source_id in source_ids
        for source in [sources_by_id[source_id]]
        for segment in sorted(source.segments, key=lambda item: item.position)
    ]


def _base_editor_input(input_json: dict[str, Any]) -> dict[str, Any]:
    """Project Build/Revision task payloads onto the common Editor contract."""

    projected = {
        field_name: input_json[field_name]
        for field_name in PodcastDraftTaskInput.model_fields
        if field_name != "task_kind" and field_name in input_json
    }
    projected["task_kind"] = BUILD_PODCAST_DRAFT
    return projected


def _writing_style_context_is_ready(input_json: dict[str, Any]) -> bool:
    profile = input_json.get("writing_style_profile")
    return bool(
        isinstance(profile, dict)
        and isinstance(profile.get("readiness"), dict)
        and profile["readiness"].get("status") == "ready"
    )


def _revision_request_matches(
    *,
    request: CreateDraftRevisionRequest,
    record: DraftRevisionRequestRecord,
    parent_run_id: str,
    plan_artifact_id: str,
) -> bool:
    version_matches = record.version == request.version or (
        record.version == LEGACY_DRAFT_REVISION_REQUEST_VERSION
        and "version" not in request.model_fields_set
        and request.supplemental_interview_plan_artifact_id is None
        and not request.answered_question_ids
    )
    return (
        version_matches
        and record.submission_id == request.submission_id
        and record.parent_run_id == parent_run_id
        and record.plan_artifact_id == plan_artifact_id
        and record.selected_actions == request.selected_actions
        and record.selected_feedback_artifact_ids == request.selected_feedback_artifact_ids
        and record.selected_gap_codes == request.selected_gap_codes
        and record.source_ids == request.source_ids
        and record.supplemental_interview_plan_artifact_id
        == request.supplemental_interview_plan_artifact_id
        and record.answered_question_ids == request.answered_question_ids
        and record.target_duration_minutes == request.target_duration_minutes
        and record.revision_instruction == request.revision_instruction
    )


def _supplemental_interview_round(input_json: dict[str, Any]) -> int:
    """Read only the server-owned bounded round counter from durable Run input."""

    value = input_json.get("supplemental_interview_round", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DraftRevisionNotAllowed("parent supplemental interview round provenance is invalid")
    if value < 0 or value > MAX_SUPPLEMENTAL_INTERVIEW_ROUNDS:
        raise DraftRevisionNotAllowed("parent supplemental interview round provenance is invalid")
    return value
