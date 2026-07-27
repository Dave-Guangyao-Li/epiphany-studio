from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from epiphany.db import Database
from epiphany.events import append_event
from epiphany.models import Event, Run, Source, Task
from epiphany.research_schemas import EpisodeResearchPayload
from epiphany.runtime.orchestrator import Orchestrator
from epiphany.schemas import ArtifactView, EventView, ModelCallView, RunView, TaskView
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


class RunService:
    def __init__(self, database: Database, orchestrator: Orchestrator) -> None:
        self.database = database
        self.orchestrator = orchestrator

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
            run = Run(
                workflow_type=workflow_type,
                workflow_version="v1",
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

    async def cancel_run(self, run_id: str) -> RunView:
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
