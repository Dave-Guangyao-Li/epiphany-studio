from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from epiphany.db import Database
from epiphany.events import append_event
from epiphany.human_input_schemas import INTERVIEW_SCAFFOLD_CHECKPOINT
from epiphany.ids import stable_id
from epiphany.interview_markdown import render_interview_scaffold_markdown
from epiphany.models import Artifact, Event, Run, Source, Task
from epiphany.research_schemas import EpisodeResearchPayload
from epiphany.runtime.orchestrator import INTERVIEW_RESEARCH_WORKFLOW_VERSION, Orchestrator
from epiphany.schemas import (
    ArtifactView,
    EventView,
    ModelCallView,
    ResumeRunResponse,
    RunView,
    TaskView,
)
from epiphany.state_machine import (
    RunStatus,
    TaskStatus,
    validate_run_transition,
    validate_task_transition,
)

logger = logging.getLogger("epiphany.run_service")


class RunNotFound(LookupError):
    pass


class RunAlreadyTerminal(ValueError):
    pass


class InvalidRunPayload(ValueError):
    pass


class RunSourceNotFound(LookupError):
    pass


class InterviewScaffoldExportNotReady(ValueError):
    pass


class RunResumeNotAllowed(ValueError):
    pass


class RunResumeConflict(ValueError):
    pass


class RunService:
    def __init__(self, database: Database, orchestrator: Orchestrator) -> None:
        self.database = database
        self.orchestrator = orchestrator
        # The MVP is explicitly single-process. Every Run mutation that can
        # compete at a human checkpoint shares this lock, so Resume and Cancel
        # cannot both cross the same waiting-state boundary. The Artifact
        # unique key remains the durable duplicate-data guard for Resume in
        # SQLite. Cross-process replay semantics are a later deployment concern.
        self._run_mutation_lock = asyncio.Lock()

    async def create_run(
        self,
        *,
        workflow_type: str,
        payload: dict[str, object],
    ) -> RunView:
        async with self.database.sessions() as session, session.begin():
            research_source_segments: list[dict[str, str]] | None = None
            if workflow_type == "episode-research":
                try:
                    research_payload = EpisodeResearchPayload.model_validate(payload)
                except ValidationError as error:
                    raise InvalidRunPayload("invalid episode-research payload") from error

                sources = (
                    (
                        await session.execute(
                            select(Source)
                            .where(Source.id.in_(research_payload.source_ids))
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

                payload = research_payload.model_dump(mode="json")
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
                INTERVIEW_RESEARCH_WORKFLOW_VERSION if workflow_type == "episode-research" else "v1"
            )
            run = Run(
                workflow_type=workflow_type,
                workflow_version=workflow_version,
                status=RunStatus.QUEUED,
                current_step=initial_step,
                input_json=payload,
            )
            session.add(run)
            await session.flush()
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

    async def export_interview_scaffold_markdown(self, run_id: str) -> str:
        async with self.database.sessions() as session:
            run = await session.get(Run, run_id)
            if run is None:
                raise RunNotFound(run_id)
            if (
                run.status not in {RunStatus.WAITING_FOR_USER, RunStatus.SUCCEEDED}
                or run.output_artifact_id is None
            ):
                raise InterviewScaffoldExportNotReady("interview scaffold is not ready for export")

            artifact = await session.get(Artifact, run.output_artifact_id)
            if (
                artifact is None
                or artifact.run_id != run.id
                or artifact.kind != "build_interview_scaffold_result"
            ):
                raise InterviewScaffoldExportNotReady("run output is not an interview scaffold")

            # Worker metadata belongs to runtime tracing, not the strict product
            # artifact rendered for the user.
            content = {
                key: value for key, value in artifact.content_json.items() if key != "_execution"
            }
            try:
                markdown = render_interview_scaffold_markdown(content)
            except (ValueError, TypeError) as error:
                raise InterviewScaffoldExportNotReady(
                    "interview scaffold output is invalid"
                ) from error

        logger.info(
            "Interview scaffold Markdown exported",
            extra={
                "event": "run.interview_scaffold_markdown.exported",
                "run_id": run_id,
                "artifact_id": artifact.id,
                "markdown_char_count": len(markdown),
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
                        or run.workflow_version != INTERVIEW_RESEARCH_WORKFLOW_VERSION
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
                    submission = Artifact(
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
                    submission_artifact_id = submission.id

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

                    # M3.1 deliberately stops after proving the durable human
                    # checkpoint. M3.2 will replace this deterministic terminal
                    # step with an Editor Task.
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
