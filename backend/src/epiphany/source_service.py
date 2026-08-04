from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from epiphany.db import Database
from epiphany.ids import stable_id
from epiphany.models import Source, SourceSegment
from epiphany.schemas import (
    ImportSourceResponse,
    SourceSegmentView,
    SourceSummaryView,
    SourceView,
)
from epiphany.source_segmentation import SegmentationResult, segment_source_text

logger = logging.getLogger("epiphany.source_service")


class SourceNotFound(LookupError):
    pass


class SourceService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def import_text(
        self,
        *,
        title: str,
        source_type: str,
        text: str,
        metadata: dict[str, object],
    ) -> ImportSourceResponse:
        segmentation = segment_source_text(text)
        created = False
        try:
            async with self.database.sessions() as session, session.begin():
                result = await self.import_text_in_session(
                    session,
                    title=title,
                    source_type=source_type,
                    text=text,
                    metadata=metadata,
                    segmentation=segmentation,
                )
                created = result.created
                view = result.source
        except IntegrityError:
            # A concurrent retry may win the unique-content insert after our
            # initial read. Re-read the committed Source and return it.
            async with self.database.sessions() as session:
                source = await self._find_by_hash(session, segmentation.content_sha256)
                if source is None:
                    raise
                view = self._to_view(source)
                created = False

        logger.info(
            "Source imported" if created else "Source import deduplicated",
            extra={
                "event": "source.imported" if created else "source.import.deduplicated",
                "source_id": view.id,
                "source_type": view.source_type,
                "char_count": view.char_count,
                "segment_count": view.segment_count,
            },
        )
        return ImportSourceResponse(created=created, source=view)

    async def import_text_in_session(
        self,
        session: AsyncSession,
        *,
        title: str,
        source_type: str,
        text: str,
        metadata: dict[str, object],
        segmentation: SegmentationResult | None = None,
    ) -> ImportSourceResponse:
        """Import or deduplicate a Source inside the caller's transaction.

        This lower-level primitive deliberately does not commit, catch an
        ``IntegrityError``, or emit a success log.  It lets a workflow make the
        Source, its Project link, provenance Artifact, events, and Run state one
        atomic durable change.  The public ``import_text`` method remains the
        convenient standalone transaction boundary.
        """

        resolved_segmentation = segmentation or segment_source_text(text)
        source = await self._find_by_hash(session, resolved_segmentation.content_sha256)
        created = source is None
        if source is None:
            source = self._build_source(
                title=title.strip(),
                source_type=source_type,
                metadata=metadata,
                segmentation=resolved_segmentation,
            )
            session.add(source)
            await session.flush()
        return ImportSourceResponse(created=created, source=self._to_view(source))

    async def get_source(self, source_id: str) -> SourceView:
        async with self.database.sessions() as session:
            source = (
                await session.execute(
                    select(Source)
                    .where(Source.id == source_id)
                    .options(selectinload(Source.segments))
                )
            ).scalar_one_or_none()
            if source is None:
                raise SourceNotFound(source_id)
            return self._to_view(source)

    async def list_sources(self, *, limit: int = 50) -> list[SourceSummaryView]:
        async with self.database.sessions() as session:
            segment_counts = (
                select(
                    SourceSegment.source_id,
                    func.count(SourceSegment.id).label("segment_count"),
                )
                .group_by(SourceSegment.source_id)
                .subquery()
            )
            statement = (
                select(Source, segment_counts.c.segment_count)
                .outerjoin(segment_counts, Source.id == segment_counts.c.source_id)
                .order_by(Source.created_at.desc(), Source.id)
                .limit(limit)
            )
            rows = (await session.execute(statement)).all()
            return [
                self._to_summary(source, segment_count=segment_count or 0)
                for source, segment_count in rows
            ]

    @staticmethod
    def _build_source(
        *,
        title: str,
        source_type: str,
        metadata: dict[str, object],
        segmentation: SegmentationResult,
    ) -> Source:
        source_id = stable_id("src", segmentation.content_sha256)
        return Source(
            id=source_id,
            title=title,
            source_type=source_type,
            content_text=segmentation.normalized_text,
            content_sha256=segmentation.content_sha256,
            char_count=len(segmentation.normalized_text),
            metadata_json=metadata,
            segments=[
                SourceSegment(
                    id=segment.id,
                    position=segment.position,
                    text=segment.text,
                    char_start=segment.char_start,
                    char_end=segment.char_end,
                    content_sha256=segment.content_sha256,
                )
                for segment in segmentation.segments
            ],
        )

    @staticmethod
    async def _find_by_hash(
        session: AsyncSession,
        content_sha256: str,
    ) -> Source | None:
        return (
            await session.execute(
                select(Source)
                .where(Source.content_sha256 == content_sha256)
                .options(selectinload(Source.segments))
            )
        ).scalar_one_or_none()

    @classmethod
    def _to_view(cls, source: Source) -> SourceView:
        segments = sorted(source.segments, key=lambda item: item.position)
        summary = cls._to_summary(source, segment_count=len(segments))
        return SourceView(
            **summary.model_dump(),
            segments=[SourceSegmentView.model_validate(segment) for segment in segments],
        )

    @staticmethod
    def _to_summary(source: Source, *, segment_count: int) -> SourceSummaryView:
        return SourceSummaryView(
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
