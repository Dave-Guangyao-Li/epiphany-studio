from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateRunRequest(BaseModel):
    workflow_type: Literal["fake-podcast"] = "fake-podcast"
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


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
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


class EventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    task_id: str | None
    sequence: int
    type: str
    payload: dict[str, Any]
    created_at: datetime
