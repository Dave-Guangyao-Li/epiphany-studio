from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from epiphany.draft_feedback_schemas import (
    DraftUserFeedbackRecord,
    DraftUserFeedbackRequest,
    DraftUserFeedbackResponse,
)
from epiphany.draft_quality_schemas import DraftQualityReportRecord
from epiphany.event_stream import stream_run_events
from epiphany.human_input_schemas import ResumeRunRequest
from epiphany.project_service import ProjectNotFound
from epiphany.revision_schemas import (
    CreateDraftRevisionRequest,
    CreateDraftRevisionResponse,
    DraftImprovementPlanRecord,
    DraftRevisionComparisonRecord,
)
from epiphany.schemas import (
    CreateRunRequest,
    EventView,
    ResumeRunResponse,
    RunSummaryView,
    RunView,
)
from epiphany.services import (
    DraftFeedbackConflict,
    DraftFeedbackNotAllowed,
    DraftImprovementPlanNotReady,
    DraftQualityReportNotReady,
    DraftRevisionComparisonNotReady,
    DraftRevisionConflict,
    DraftRevisionNotAllowed,
    InterviewScaffoldExportNotReady,
    InvalidRunPayload,
    PodcastDraftExportNotReady,
    ProjectSourceNotLinked,
    RunAlreadyTerminal,
    RunNotFound,
    RunResumeConflict,
    RunResumeNotAllowed,
    RunService,
    RunSourceNotFound,
    SupplementalInterviewPlanNotReady,
)
from epiphany.supplemental_interview_schemas import SupplementalInterviewPlanRecord

router = APIRouter()


def get_run_service(request: Request) -> RunService:
    return request.app.state.run_service


RunServiceDependency = Annotated[RunService, Depends(get_run_service)]
EventSequenceQuery = Annotated[int, Query(ge=0)]
RunLimitQuery = Annotated[int, Query(ge=1, le=100)]
ProjectIdQuery = Annotated[str | None, Query()]
LastEventIdHeader = Annotated[str | None, Header(alias="Last-Event-ID")]


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


@router.get("/runs", response_model=list[RunSummaryView])
async def list_runs(
    service: RunServiceDependency,
    project_id: ProjectIdQuery = None,
    limit: RunLimitQuery = 100,
) -> list[RunSummaryView]:
    try:
        return await service.list_runs(project_id=project_id, limit=limit)
    except ProjectNotFound as error:
        raise HTTPException(status_code=404, detail="project not found") from error


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(
    run_id: str,
    service: RunServiceDependency,
) -> RunView:
    try:
        return await service.get_run(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error


@router.get(
    "/runs/{run_id}/exports/interview-scaffold.md",
    response_class=Response,
)
async def export_interview_scaffold_markdown(
    run_id: str,
    service: RunServiceDependency,
) -> Response:
    try:
        markdown = await service.export_interview_scaffold_markdown(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except InterviewScaffoldExportNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": (f'attachment; filename="interview-scaffold-{run_id}.md"')},
    )


@router.get(
    "/runs/{run_id}/exports/podcast-draft.md",
    response_class=Response,
)
async def export_podcast_draft_markdown(
    run_id: str,
    service: RunServiceDependency,
) -> Response:
    try:
        markdown = await service.export_podcast_draft_markdown(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except PodcastDraftExportNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": (f'attachment; filename="podcast-draft-{run_id}.md"')},
    )


@router.get(
    "/runs/{run_id}/exports/show-notes.md",
    response_class=Response,
)
async def export_show_notes_markdown(
    run_id: str,
    service: RunServiceDependency,
) -> Response:
    try:
        markdown = await service.export_show_notes_markdown(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except PodcastDraftExportNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": (f'attachment; filename="show-notes-{run_id}.md"')},
    )


@router.get(
    "/runs/{run_id}/quality-report",
    response_model=DraftQualityReportRecord,
)
async def get_draft_quality_report(
    run_id: str,
    service: RunServiceDependency,
) -> DraftQualityReportRecord:
    try:
        return await service.get_draft_quality_report(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except DraftQualityReportNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get(
    "/runs/{run_id}/improvement-plan",
    response_model=DraftImprovementPlanRecord,
)
async def get_draft_improvement_plan(
    run_id: str,
    service: RunServiceDependency,
) -> DraftImprovementPlanRecord:
    try:
        return await service.get_draft_improvement_plan(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except DraftImprovementPlanNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get(
    "/runs/{run_id}/supplemental-interview-plan",
    response_model=SupplementalInterviewPlanRecord,
)
async def get_supplemental_interview_plan(
    run_id: str,
    service: RunServiceDependency,
) -> SupplementalInterviewPlanRecord:
    try:
        return await service.get_supplemental_interview_plan(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except SupplementalInterviewPlanNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/runs/{run_id}/revisions",
    response_model=CreateDraftRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_draft_revision(
    run_id: str,
    body: CreateDraftRevisionRequest,
    service: RunServiceDependency,
    response: Response,
) -> CreateDraftRevisionResponse:
    try:
        result = await service.create_draft_revision(run_id, request=body)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except RunSourceNotFound as error:
        raise HTTPException(status_code=404, detail=f"source not found: {error}") from error
    except (
        DraftImprovementPlanNotReady,
        ProjectSourceNotLinked,
        DraftRevisionNotAllowed,
        DraftRevisionConflict,
    ) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
    return result


@router.get(
    "/runs/{run_id}/revision-comparison",
    response_model=DraftRevisionComparisonRecord,
)
async def get_draft_revision_comparison(
    run_id: str,
    service: RunServiceDependency,
) -> DraftRevisionComparisonRecord:
    try:
        return await service.get_draft_revision_comparison(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except DraftRevisionComparisonNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get(
    "/runs/{run_id}/exports/quality-report.md",
    response_class=Response,
)
async def export_draft_quality_markdown(
    run_id: str,
    service: RunServiceDependency,
) -> Response:
    try:
        markdown = await service.export_draft_quality_markdown(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except DraftQualityReportNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="quality-report-{run_id}.md"'},
    )


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


@router.get("/runs/{run_id}/events/stream")
async def stream_events(
    run_id: str,
    request: Request,
    service: RunServiceDependency,
    after: EventSequenceQuery = 0,
    last_event_id: LastEventIdHeader = None,
) -> StreamingResponse:
    header_sequence = 0
    if last_event_id:
        try:
            header_sequence = int(last_event_id)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail="Last-Event-ID must be an integer",
            ) from error
        if header_sequence < 0:
            raise HTTPException(status_code=422, detail="Last-Event-ID must be non-negative")

    # Resolve the Run before returning StreamingResponse so a missing ID is a
    # normal JSON 404 instead of a half-open 200 stream.
    try:
        await service.get_run(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error

    cursor = max(after, header_sequence)
    return StreamingResponse(
        stream_run_events(
            request=request,
            service=service,
            run_id=run_id,
            after=cursor,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/runs/{run_id}/quality-feedback",
    response_model=DraftUserFeedbackResponse,
)
async def submit_draft_feedback(
    run_id: str,
    body: DraftUserFeedbackRequest,
    service: RunServiceDependency,
) -> DraftUserFeedbackResponse:
    try:
        return await service.submit_draft_feedback(run_id, feedback=body)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except (DraftFeedbackNotAllowed, DraftFeedbackConflict) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get(
    "/runs/{run_id}/quality-feedback",
    response_model=list[DraftUserFeedbackRecord],
)
async def list_draft_feedback(
    run_id: str,
    service: RunServiceDependency,
) -> list[DraftUserFeedbackRecord]:
    try:
        return await service.list_draft_feedback(run_id)
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except DraftFeedbackConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/runs/{run_id}/resume", response_model=ResumeRunResponse)
async def resume_run(
    run_id: str,
    body: ResumeRunRequest,
    service: RunServiceDependency,
) -> ResumeRunResponse:
    try:
        return await service.resume_run(
            run_id,
            checkpoint=body.checkpoint,
            submission_id=body.submission_id,
            source_ids=body.source_ids,
        )
    except RunNotFound as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except RunSourceNotFound as error:
        raise HTTPException(status_code=404, detail=f"source not found: {error}") from error
    except (RunResumeNotAllowed, RunResumeConflict) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
