from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from epiphany.schemas import RunSummaryView, SourceSummaryView, SourceView


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5_000)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("title must contain non-whitespace characters")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ProjectSummaryView(BaseModel):
    id: str
    title: str
    description: str | None
    source_count: int
    run_count: int
    created_at: datetime
    updated_at: datetime


class ProjectView(ProjectSummaryView):
    sources: list[SourceSummaryView]
    runs: list[RunSummaryView]


class ProjectSourceImportResponse(BaseModel):
    created: bool
    linked: bool
    source: SourceView


class CreateProjectRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: str = Field(min_length=1, max_length=160)
    workflow_type: Literal["fake-podcast", "episode-research"] = "episode-research"
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("submission_id")
    @classmethod
    def submission_id_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("submission_id must contain non-whitespace characters")
        return normalized
