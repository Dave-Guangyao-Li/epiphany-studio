from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from epiphany.schemas import (
    CreateSourceRequest,
    ImportSourceResponse,
    SourceSummaryView,
    SourceView,
)
from epiphany.source_service import SourceNotFound, SourceService

router = APIRouter(prefix="/sources", tags=["sources"])


def get_source_service(request: Request) -> SourceService:
    return request.app.state.source_service


SourceServiceDependency = Annotated[SourceService, Depends(get_source_service)]
SourceLimitQuery = Annotated[int, Query(ge=1, le=100)]


@router.post("", response_model=ImportSourceResponse, status_code=status.HTTP_201_CREATED)
async def import_source(
    body: CreateSourceRequest,
    response: Response,
    service: SourceServiceDependency,
) -> ImportSourceResponse:
    result = await service.import_text(
        title=body.title,
        source_type=body.source_type,
        text=body.text,
        metadata=body.metadata,
    )
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return result


@router.get("", response_model=list[SourceSummaryView])
async def list_sources(
    service: SourceServiceDependency,
    limit: SourceLimitQuery = 50,
) -> list[SourceSummaryView]:
    return await service.list_sources(limit=limit)


@router.get("/{source_id}", response_model=SourceView)
async def get_source(
    source_id: str,
    service: SourceServiceDependency,
) -> SourceView:
    try:
        return await service.get_source(source_id)
    except SourceNotFound as error:
        raise HTTPException(status_code=404, detail="source not found") from error
