from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

INTERVIEW_SCAFFOLD_CHECKPOINT = "interview_scaffold"

HumanInputCheckpoint = Literal["interview_scaffold"]


class ResumeRunRequest(BaseModel):
    """User material submitted while a Run is paused at a human checkpoint."""

    model_config = ConfigDict(extra="forbid")

    checkpoint: HumanInputCheckpoint
    submission_id: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("submission_id", mode="before")
    @classmethod
    def normalize_submission_id(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return " ".join(value.split())

    @field_validator("source_ids")
    @classmethod
    def normalize_and_validate_source_ids(cls, value: list[str]) -> list[str]:
        normalized = [source_id.strip() for source_id in value]
        if any(not source_id for source_id in normalized):
            raise ValueError("source_ids must not contain blank values")
        if any(len(source_id) > 200 for source_id in normalized):
            raise ValueError("source_ids must not exceed 200 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("source_ids must be unique")
        return normalized
