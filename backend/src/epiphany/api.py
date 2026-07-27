from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from epiphany.schemas import CreateRunRequest, EventView, RunView
from epiphany.services import (
    InvalidRunPayload,
    RunAlreadyTerminal,
    RunNotFound,
    RunService,
    RunSourceNotFound,
)

router = APIRouter()


def get_run_service(request: Request) -> RunService:
    return request.app.state.run_service


RunServiceDependency = Annotated[RunService, Depends(get_run_service)]
EventSequenceQuery = Annotated[int, Query(ge=0)]


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/runs", response_model=RunView, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: CreateRunRequest,
    service: RunServiceDependency,
) -> RunView:
    try:
        return await service.create_run(
            workflow_type=body.workflow_type,
            payload=body.payload,
        )
    except InvalidRunPayload as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RunSourceNotFound as error:
        raise HTTPException(status_code=404, detail=f"source not found: {error}") from error


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(
    run_id: str,
    service: RunServiceDependency,
) -> RunView:
    try:
        return await service.get_run(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error


@router.get("/runs/{run_id}/events", response_model=list[EventView])
async def get_events(
    run_id: str,
    service: RunServiceDependency,
    after: EventSequenceQuery = 0,
) -> list[EventView]:
    try:
        return await service.list_events(run_id, after=after)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error


@router.post("/runs/{run_id}/cancel", response_model=RunView)
async def cancel_run(
    run_id: str,
    service: RunServiceDependency,
) -> RunView:
    try:
        return await service.cancel_run(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except RunAlreadyTerminal as error:
        raise HTTPException(status_code=409, detail=f"run is already {error}") from error
