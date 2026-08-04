from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from epiphany.db import Database
from epiphany.events import append_event
from epiphany.models import Artifact, Project, ProjectSource, Run, Source, SourceSegment
from epiphany.project_schemas import (
    ProjectSourceImportResponse,
    ProjectSummaryView,
    ProjectView,
)
from epiphany.schemas import RunSummaryView, SourceSummaryView
from epiphany.source_service import SourceService
from epiphany.source_starter_schemas import (
    SOURCE_STARTER_WORKFLOW_TYPE,
    SOURCE_STARTER_WORKFLOW_VERSION,
    SourceStarterConfirmationResponse,
)
from epiphany.state_machine import RunStatus, validate_run_transition

logger = logging.getLogger("epiphany.project_service")


class ProjectNotFound(LookupError):
    pass


class SourceStarterNotFound(LookupError):
    pass


class SourceStarterConfirmationNotAllowed(ValueError):
    pass


class SourceStarterConfirmationConflict(ValueError):
    pass


@dataclass(frozen=True)
class _SourceStarterConfirmationOutcome:
    created: bool
    linked: bool
    idempotent_replay: bool
    source_id: str
    candidate_artifact_id: str
    confirmation_artifact_id: str


class ProjectService:
    def __init__(
        self,
        database: Database,
        source_service: SourceService,
        *,
        mutation_lock: asyncio.Lock | None = None,
    ) -> None:
        self.database = database
        self.source_service = source_service
        self._mutation_lock = asyncio.Lock()
        # Confirmation and generic Run cancellation must serialize through the
        # same in-process lock.  SQLite remains the durable transaction and
        # unique-key boundary; the shared lock removes a stale-status race in
        # the explicitly single-process MVP.
        self._source_starter_confirmation_lock = mutation_lock or asyncio.Lock()

    async def create_project(
        self,
        *,
        title: str,
        description: str | None,
    ) -> ProjectSummaryView:
        async with self.database.sessions() as session, session.begin():
            project = Project(title=title, description=description)
            session.add(project)
            await session.flush()
            result = self._to_summary(project, source_count=0, run_count=0)

        logger.info(
            "Project created",
            extra={"event": "project.created", "project_id": result.id},
        )
        return result

    async def list_projects(self, *, limit: int = 100) -> list[ProjectSummaryView]:
        async with self.database.sessions() as session:
            source_counts = (
                select(
                    ProjectSource.project_id,
                    func.count(ProjectSource.source_id).label("source_count"),
                )
                .group_by(ProjectSource.project_id)
                .subquery()
            )
            run_counts = (
                select(Run.project_id, func.count(Run.id).label("run_count"))
                .where(Run.project_id.is_not(None))
                .group_by(Run.project_id)
                .subquery()
            )
            rows = (
                await session.execute(
                    select(
                        Project,
                        func.coalesce(source_counts.c.source_count, 0),
                        func.coalesce(run_counts.c.run_count, 0),
                    )
                    .outerjoin(source_counts, Project.id == source_counts.c.project_id)
                    .outerjoin(run_counts, Project.id == run_counts.c.project_id)
                    .order_by(Project.created_at.desc(), Project.id)
                    .limit(limit)
                )
            ).all()
            return [
                self._to_summary(project, source_count=source_count, run_count=run_count)
                for project, source_count, run_count in rows
            ]

    async def get_project(self, project_id: str) -> ProjectView:
        async with self.database.sessions() as session:
            project = await session.get(Project, project_id)
            if project is None:
                raise ProjectNotFound(project_id)

            segment_counts = (
                select(
                    SourceSegment.source_id,
                    func.count(SourceSegment.id).label("segment_count"),
                )
                .group_by(SourceSegment.source_id)
                .subquery()
            )
            source_rows = (
                await session.execute(
                    select(Source, func.coalesce(segment_counts.c.segment_count, 0))
                    .join(ProjectSource, ProjectSource.source_id == Source.id)
                    .outerjoin(segment_counts, Source.id == segment_counts.c.source_id)
                    .where(ProjectSource.project_id == project_id)
                    .order_by(ProjectSource.created_at.desc(), Source.id)
                )
            ).all()
            runs = (
                (
                    await session.execute(
                        select(Run)
                        .where(Run.project_id == project_id)
                        .order_by(Run.created_at.desc(), Run.id)
                    )
                )
                .scalars()
                .all()
            )
            sources = [
                SourceSummaryView(
                    id=source.id,
                    title=source.title,
                    source_type=source.source_type,
                    content_sha256=source.content_sha256,
                    char_count=source.char_count,
                    segment_count=segment_count,
                    metadata=source.metadata_json,
                    created_at=source.created_at,
                    updated_at=source.updated_at,
                )
                for source, segment_count in source_rows
            ]
            summary = self._to_summary(
                project,
                source_count=len(sources),
                run_count=len(runs),
            )
            return ProjectView(
                **summary.model_dump(),
                sources=sources,
                runs=[RunSummaryView.model_validate(run) for run in runs],
            )

    async def import_source(
        self,
        project_id: str,
        *,
        title: str,
        source_type: str,
        text: str,
        metadata: dict[str, object],
    ) -> ProjectSourceImportResponse:
        await self._require_project(project_id)
        imported = await self.source_service.import_text(
            title=title,
            source_type=source_type,
            text=text,
            metadata=metadata,
        )
        linked = False
        async with self._mutation_lock:
            async with self.database.sessions() as session, session.begin():
                project = await session.get(Project, project_id)
                if project is None:
                    raise ProjectNotFound(project_id)
                key = {"project_id": project_id, "source_id": imported.source.id}
                if await session.get(ProjectSource, key) is None:
                    session.add(ProjectSource(**key))
                    linked = True

        logger.info(
            "Source linked to Project" if linked else "Project Source link replayed",
            extra={
                "event": "project.source.linked" if linked else "project.source.link_replayed",
                "project_id": project_id,
                "source_id": imported.source.id,
                "source_created": imported.created,
            },
        )
        return ProjectSourceImportResponse(
            created=imported.created,
            linked=linked,
            source=imported.source,
        )

    async def confirm_source_starter(
        self,
        project_id: str,
        run_id: str,
        *,
        submission_id: str,
        title: str,
        source_type: str,
        text: str,
    ) -> SourceStarterConfirmationResponse:
        """Atomically turn an edited candidate into evidence and complete its Run."""

        request_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "title": title,
                    "source_type": source_type,
                    "text": text,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        confirmation_key = f"source-starter-confirmation:{run_id}"

        async with self._source_starter_confirmation_lock:
            # A concurrent process may win either the content-hash Source insert
            # or the confirmation Artifact unique key.  Because the whole
            # operation is one transaction, retrying can safely observe the
            # winner; a crash never leaves a half-confirmed Source behind.
            for attempt in range(2):
                try:
                    outcome = await self._confirm_source_starter_transaction(
                        project_id=project_id,
                        run_id=run_id,
                        submission_id=submission_id,
                        title=title,
                        source_type=source_type,
                        text=text,
                        request_fingerprint=request_fingerprint,
                        confirmation_key=confirmation_key,
                    )
                    break
                except IntegrityError:
                    if attempt == 1:
                        raise
            else:  # pragma: no cover - the bounded loop always breaks or raises.
                raise RuntimeError("source-starter confirmation retry was exhausted")

        source = await self.source_service.get_source(outcome.source_id)

        logger.info(
            (
                "Source starter confirmation replayed"
                if outcome.idempotent_replay
                else "Source starter confirmed"
            ),
            extra={
                "event": (
                    "project.source_starter.confirmation_replayed"
                    if outcome.idempotent_replay
                    else "project.source_starter.confirmed"
                ),
                "project_id": project_id,
                "run_id": run_id,
                "source_id": outcome.source_id,
                "artifact_id": outcome.confirmation_artifact_id,
            },
        )
        return SourceStarterConfirmationResponse(
            created=outcome.created,
            linked=outcome.linked,
            idempotent_replay=outcome.idempotent_replay,
            source=source,
            source_starter_run_id=run_id,
            candidate_artifact_id=outcome.candidate_artifact_id,
            confirmation_artifact_id=outcome.confirmation_artifact_id,
        )

    async def _confirm_source_starter_transaction(
        self,
        *,
        project_id: str,
        run_id: str,
        submission_id: str,
        title: str,
        source_type: str,
        text: str,
        request_fingerprint: str,
        confirmation_key: str,
    ) -> _SourceStarterConfirmationOutcome:
        async with self.database.sessions() as session, session.begin():
            project = await session.get(Project, project_id)
            if project is None:
                raise ProjectNotFound(project_id)
            run = await session.get(Run, run_id)
            if (
                run is None
                or run.project_id != project_id
                or run.workflow_type != SOURCE_STARTER_WORKFLOW_TYPE
                or run.workflow_version != SOURCE_STARTER_WORKFLOW_VERSION
            ):
                raise SourceStarterNotFound(run_id)
            candidate = (
                await session.get(Artifact, run.output_artifact_id)
                if run.output_artifact_id is not None
                else None
            )
            if candidate is None or candidate.kind != "source_starter_candidate":
                raise SourceStarterConfirmationNotAllowed(
                    "source-starter candidate artifact is unavailable"
                )
            if candidate.content_json.get("source_type") != source_type:
                raise SourceStarterConfirmationNotAllowed(
                    "confirmed source_type must match the generated candidate"
                )

            existing = (
                await session.execute(
                    select(Artifact).where(Artifact.idempotency_key == confirmation_key)
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.content_json.get("request_fingerprint") != request_fingerprint:
                    raise SourceStarterConfirmationConflict(
                        "source-starter candidate was already confirmed with different content"
                    )
                source_id = str(existing.content_json.get("source_id", ""))
                source = (
                    await session.execute(
                        select(Source)
                        .where(Source.id == source_id)
                        .options(selectinload(Source.segments))
                    )
                ).scalar_one_or_none()
                link = await session.get(
                    ProjectSource,
                    {"project_id": project_id, "source_id": source_id},
                )
                if source is None or link is None:
                    raise SourceStarterConfirmationConflict(
                        "persisted source-starter confirmation is incomplete"
                    )
                recorded_ids = existing.content_json.get("submission_ids")
                submission_ids = (
                    [str(value) for value in recorded_ids if isinstance(value, str)]
                    if isinstance(recorded_ids, list)
                    else [str(existing.content_json.get("submission_id", ""))]
                )
                submission_ids = [value for value in submission_ids if value]
                if submission_id not in submission_ids:
                    submission_ids.append(submission_id)
                    existing.content_json = {
                        **existing.content_json,
                        "submission_ids": submission_ids,
                        "last_submission_id": submission_id,
                    }
                return _SourceStarterConfirmationOutcome(
                    created=False,
                    linked=False,
                    idempotent_replay=True,
                    source_id=source.id,
                    candidate_artifact_id=candidate.id,
                    confirmation_artifact_id=existing.id,
                )

            legacy_completed_candidate = (
                run.status == RunStatus.SUCCEEDED and run.current_step == "complete"
            )
            if not (
                (
                    run.status == RunStatus.WAITING_FOR_USER
                    and run.current_step == "awaiting_source_confirmation"
                )
                or legacy_completed_candidate
            ):
                raise SourceStarterConfirmationNotAllowed(
                    "source-starter Run is not waiting for candidate confirmation"
                )

            execution = candidate.content_json.get("_execution")
            imported = await self.source_service.import_text_in_session(
                session,
                title=title,
                source_type=source_type,
                text=text,
                metadata={
                    "origin": "ai_assisted",
                    "user_confirmed": True,
                    "source_starter_run_id": run_id,
                    "source_starter_artifact_id": candidate.id,
                    "generated_by": execution if isinstance(execution, dict) else {},
                },
            )
            if not imported.created:
                # Source content hashes are globally unique.  Reusing an older
                # Source here would also reuse its source_type and metadata,
                # silently discarding this confirmation's server-owned
                # ``ai_assisted`` provenance.  A legitimate retry is handled
                # above by the confirmation Artifact idempotency key, so any
                # pre-existing Source at this point is an incompatible
                # collision and must remain untouched.
                raise SourceStarterConfirmationConflict(
                    "confirmed content already exists as an incompatible Source"
                )
            key = {"project_id": project_id, "source_id": imported.source.id}
            linked = await session.get(ProjectSource, key) is None
            if linked:
                session.add(ProjectSource(**key))

            confirmation = Artifact(
                run_id=run_id,
                task_id=None,
                kind="source_starter_confirmation",
                content_json={
                    "schema_version": "source-starter-confirmation.v1",
                    "submission_id": submission_id,
                    "submission_ids": [submission_id],
                    "last_submission_id": submission_id,
                    "request_fingerprint": request_fingerprint,
                    "candidate_artifact_id": candidate.id,
                    "source_id": imported.source.id,
                    "confirmed_at": datetime.now(UTC).isoformat(),
                },
                idempotency_key=confirmation_key,
            )
            session.add(confirmation)
            await session.flush()

            if not legacy_completed_candidate:
                validate_run_transition(run.status, RunStatus.RUNNING)
                run.status = RunStatus.RUNNING
                validate_run_transition(run.status, RunStatus.SUCCEEDED)
                run.status = RunStatus.SUCCEEDED
                run.current_step = "complete"

            await append_event(
                session,
                run_id=run_id,
                event_type="workflow.source_starter.confirmed",
                payload={
                    "checkpoint": "source_confirmation",
                    "candidate_artifact_id": candidate.id,
                    "confirmation_artifact_id": confirmation.id,
                    "source_id": imported.source.id,
                },
            )
            if not legacy_completed_candidate:
                await append_event(
                    session,
                    run_id=run_id,
                    event_type="run.succeeded",
                    payload={"output_artifact_id": candidate.id},
                )

            return _SourceStarterConfirmationOutcome(
                created=imported.created,
                linked=linked,
                idempotent_replay=False,
                source_id=imported.source.id,
                candidate_artifact_id=candidate.id,
                confirmation_artifact_id=confirmation.id,
            )

    async def _require_project(self, project_id: str) -> None:
        async with self.database.sessions() as session:
            if await session.get(Project, project_id) is None:
                raise ProjectNotFound(project_id)

    @staticmethod
    def _to_summary(
        project: Project,
        *,
        source_count: int,
        run_count: int,
    ) -> ProjectSummaryView:
        return ProjectSummaryView(
            id=project.id,
            title=project.title,
            description=project.description,
            source_count=source_count,
            run_count=run_count,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
