from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateRunRequest(BaseModel):
    workflow_type: Literal["fake-podcast", "episode-research"] = "fake-podcast"
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    parent_task_id: str | None
    kind: str
    agent_type: str
    status: str
    attempt: int
    max_attempts: int
    output_artifact_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ArtifactView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str | None
    kind: str
    content_json: dict[str, Any]
    created_at: datetime


class ModelCallView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    attempt: int
    provider: str
    model: str
    status: str
    input_tokens: int
    output_tokens: int
    duration_ms: int | None
    estimated_cost_micros: int
    cost_currency: str
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    parent_run_id: str | None
    workflow_type: str
    workflow_version: str
    status: str
    current_step: str | None
    input_json: dict[str, Any]
    output_artifact_id: str | None
    model_call_count: int
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    tasks: list[TaskView]
    artifacts: list[ArtifactView]
    model_calls: list[ModelCallView]


class ResumeRunResponse(BaseModel):
    resumed: bool
    idempotent_replay: bool
    submission_artifact_id: str
    run: RunView


class EventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    task_id: str | None
    sequence: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


SourceType = Literal["journal", "podcast_draft", "voice_note_transcript", "other"]


class CreateSourceRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source_type: SourceType = "journal"
    text: str = Field(min_length=1, max_length=2_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must contain non-whitespace characters")
        return stripped

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain non-whitespace characters")
        return value


class SourceSegmentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    position: int
    text: str
    char_start: int
    char_end: int
    content_sha256: str
    created_at: datetime


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_segment_id: str


class SourceSummaryView(BaseModel):
    id: str
    title: str
    source_type: str
    content_sha256: str
    char_count: int
    segment_count: int
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SourceView(SourceSummaryView):
    segments: list[SourceSegmentView]


class ImportSourceResponse(BaseModel):
    created: bool
    source: SourceView
