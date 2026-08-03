from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select

from epiphany.db import Database
from epiphany.models import Project, ProjectSource, Run, Source, SourceSegment
from epiphany.project_schemas import (
    ProjectSourceImportResponse,
    ProjectSummaryView,
    ProjectView,
)
from epiphany.schemas import RunSummaryView, SourceSummaryView
from epiphany.source_service import SourceService

logger = logging.getLogger("epiphany.project_service")


class ProjectNotFound(LookupError):
    pass


class ProjectService:
    def __init__(self, database: Database, source_service: SourceService) -> None:
        self.database = database
        self.source_service = source_service
        self._mutation_lock = asyncio.Lock()

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
