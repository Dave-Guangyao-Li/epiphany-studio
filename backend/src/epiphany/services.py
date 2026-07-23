from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from epiphany.db import Database
from epiphany.events import append_event
from epiphany.models import Event, Run, Task
from epiphany.runtime.orchestrator import Orchestrator
from epiphany.schemas import ArtifactView, EventView, RunView, TaskView
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
            run = Run(
                workflow_type=workflow_type,
                workflow_version="v1",
                status=RunStatus.QUEUED,
                current_step="prepare_sources",
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
            await self.orchestrator.enqueue_initial_task(session, run)
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
                .options(selectinload(Run.tasks), selectinload(Run.artifacts))
            )
            run = (await session.execute(statement)).scalar_one_or_none()
            if run is None:
                raise RunNotFound(run_id)

            tasks = sorted(run.tasks, key=lambda item: (item.created_at, item.id))
            artifacts = sorted(run.artifacts, key=lambda item: (item.created_at, item.id))
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
