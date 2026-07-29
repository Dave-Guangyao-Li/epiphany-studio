from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from epiphany.schemas import ArtifactView

DRAFT_FEEDBACK_SCHEMA_VERSION = "draft_user_feedback_v1"
FeedbackOrigin = Literal["human", "synthetic_test"]
FeedbackDecision = Literal["accepted", "needs_revision", "rejected"]


class DraftUserFeedbackRequest(BaseModel):
    """A person's assessment of one generated podcast draft.

    ``synthetic_test`` exists only so the automated E2E can exercise the same
    API. It is deliberately ineligible for real-user product metrics.
    """

    model_config = ConfigDict(extra="forbid")

    submission_id: str = Field(min_length=1, max_length=200)
    feedback_origin: FeedbackOrigin
    decision: FeedbackDecision
    overall_rating: int = Field(ge=1, le=5)
    voice_match_rating: int = Field(ge=1, le=5)
    recordability_rating: int = Field(ge=1, le=5)
    usefulness_rating: int = Field(ge=1, le=5)
    tone_fit_rating: int = Field(ge=1, le=5)
    would_record_as_is: bool
    observed_duration_minutes: float | None = Field(default=None, gt=0, le=180)
    comment: str | None = Field(default=None, max_length=2_000)

    @field_validator("submission_id", mode="before")
    @classmethod
    def normalize_submission_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return " ".join(value.split())
        return value

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DraftUserFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["draft_user_feedback_v1"] = DRAFT_FEEDBACK_SCHEMA_VERSION
    submission_id: str = Field(min_length=1, max_length=200)
    draft_artifact_id: str = Field(min_length=1, max_length=200)
    feedback_origin: FeedbackOrigin
    human_signal_eligible: bool
    decision: FeedbackDecision
    overall_rating: int = Field(ge=1, le=5)
    voice_match_rating: int = Field(ge=1, le=5)
    recordability_rating: int = Field(ge=1, le=5)
    usefulness_rating: int = Field(ge=1, le=5)
    tone_fit_rating: int = Field(ge=1, le=5)
    would_record_as_is: bool
    observed_duration_minutes: float | None = Field(default=None, gt=0, le=180)
    comment: str | None = Field(default=None, max_length=2_000)


class DraftUserFeedbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotent_replay: bool
    feedback: DraftUserFeedback
    artifact: ArtifactView


class DraftUserFeedbackRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: DraftUserFeedback
    artifact: ArtifactView
