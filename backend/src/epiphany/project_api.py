from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from epiphany.project_schemas import (
    CreateProjectRequest,
    CreateProjectRunRequest,
    ProjectSourceImportResponse,
    ProjectSummaryView,
    ProjectView,
)
from epiphany.project_service import (
    ProjectNotFound,
    ProjectService,
    SourceStarterConfirmationConflict,
    SourceStarterConfirmationNotAllowed,
    SourceStarterNotFound,
)
from epiphany.schemas import CreateSourceRequest, RunView
from epiphany.services import (
    InvalidRunPayload,
    ProjectRunConflict,
    ProjectSourceNotLinked,
    RunService,
    RunSourceNotFound,
)
from epiphany.source_starter_schemas import (
    ConfirmSourceStarterRequest,
    CreateSourceStarterRequest,
    SourceStarterConfirmationResponse,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def get_project_service(request: Request) -> ProjectService:
    return request.app.state.project_service


def get_run_service(request: Request) -> RunService:
    return request.app.state.run_service


ProjectServiceDependency = Annotated[ProjectService, Depends(get_project_service)]
RunServiceDependency = Annotated[RunService, Depends(get_run_service)]
ProjectLimitQuery = Annotated[int, Query(ge=1, le=100)]


@router.post("", response_model=ProjectSummaryView, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: CreateProjectRequest,
    service: ProjectServiceDependency,
) -> ProjectSummaryView:
    return await service.create_project(title=body.title, description=body.description)


@router.get("", response_model=list[ProjectSummaryView])
async def list_projects(
    service: ProjectServiceDependency,
    limit: ProjectLimitQuery = 100,
) -> list[ProjectSummaryView]:
    return await service.list_projects(limit=limit)


@router.get("/{project_id}", response_model=ProjectView)
async def get_project(
    project_id: str,
    service: ProjectServiceDependency,
) -> ProjectView:
    try:
        return await service.get_project(project_id)
    except ProjectNotFound as error:
        raise HTTPException(status_code=404, detail="project not found") from error


@router.post(
    "/{project_id}/sources",
    response_model=ProjectSourceImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_project_source(
    project_id: str,
    body: CreateSourceRequest,
    response: Response,
    service: ProjectServiceDependency,
) -> ProjectSourceImportResponse:
    try:
        result = await service.import_source(
            project_id,
            title=body.title,
            source_type=body.source_type,
            text=body.text,
            metadata=body.metadata,
        )
    except ProjectNotFound as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    if not result.created and not result.linked:
        response.status_code = status.HTTP_200_OK
    return result


@router.post(
    "/{project_id}/runs",
    response_model=RunView,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_run(
    project_id: str,
    body: CreateProjectRunRequest,
    response: Response,
    service: RunServiceDependency,
) -> RunView:
    try:
        result = await service.create_project_run(
            workflow_type=body.workflow_type,
            payload=body.payload,
            project_id=project_id,
            submission_id=body.submission_id,
        )
    except ProjectNotFound as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except RunSourceNotFound as error:
        raise HTTPException(status_code=404, detail=f"source not found: {error}") from error
    except ProjectSourceNotLinked as error:
        raise HTTPException(
            status_code=409,
            detail=f"source is not linked to project: {error}",
        ) from error
    except ProjectRunConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InvalidRunPayload as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
        response.headers["X-Idempotent-Replay"] = "true"
    return result.run


@router.post(
    "/{project_id}/source-starters",
    response_model=RunView,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_source_starter(
    project_id: str,
    body: CreateSourceStarterRequest,
    response: Response,
    service: RunServiceDependency,
) -> RunView:
    try:
        result = await service.create_project_source_starter(
            project_id=project_id,
            submission_id=body.submission_id,
            source_title=body.source_title,
            source_type=body.source_type,
            mode=body.mode,
            intent=body.intent,
        )
    except ProjectNotFound as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except ProjectRunConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InvalidRunPayload as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
        response.headers["X-Idempotent-Replay"] = "true"
    return result.run


@router.post(
    "/{project_id}/source-starters/{run_id}/confirm",
    response_model=SourceStarterConfirmationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_project_source_starter(
    project_id: str,
    run_id: str,
    body: ConfirmSourceStarterRequest,
    response: Response,
    service: ProjectServiceDependency,
) -> SourceStarterConfirmationResponse:
    try:
        result = await service.confirm_source_starter(
            project_id,
            run_id,
            submission_id=body.submission_id,
            title=body.title,
            source_type=body.source_type,
            text=body.text,
        )
    except ProjectNotFound as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except SourceStarterNotFound as error:
        raise HTTPException(status_code=404, detail="source starter not found") from error
    except SourceStarterConfirmationNotAllowed as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SourceStarterConfirmationConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
        response.headers["X-Idempotent-Replay"] = "true"
    return result
