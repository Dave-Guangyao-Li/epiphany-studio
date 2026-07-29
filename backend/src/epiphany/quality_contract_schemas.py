from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_SPEAKING_RATE_CHARS_PER_MINUTE = 280
DURATION_TOLERANCE_RATIO = 0.15

TargetDurationMinutes = Literal[10, 15, 30]
DraftQualityProfile = Literal["podcast_draft_v1"]
EpisodeScenario = Literal[
    "reflective_solo",
    "narrative_solo",
    "educational_explainer",
    "conversational_diary",
]

ShortText = Annotated[str, Field(min_length=1, max_length=200)]


def _normalize_required_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("text must contain non-whitespace characters")
    return normalized


def _normalize_unique_text_list(value: list[str]) -> list[str]:
    normalized = [" ".join(item.split()) for item in value]
    if any(not item for item in normalized):
        raise ValueError("items must contain non-whitespace characters")
    if len(normalized) != len(set(normalized)):
        raise ValueError("items must be unique")
    return normalized


class CreativeBrief(BaseModel):
    """Resolved creative intent persisted with a new episode Run.

    The duration is an estimate based on non-whitespace script characters. It
    is not a promise about a future recording, whose pauses and delivery speed
    can only be measured from audio.
    """

    model_config = ConfigDict(extra="forbid")

    target_duration_minutes: TargetDurationMinutes = 10
    speaking_rate_chars_per_minute: int = Field(
        default=DEFAULT_SPEAKING_RATE_CHARS_PER_MINUTE,
        ge=120,
        le=400,
    )
    scenario: EpisodeScenario = "reflective_solo"
    target_audience: str = Field(
        default="未来的自己，以及正在经历相似转折的听众",
        min_length=1,
        max_length=500,
    )
    communication_goal: str = Field(
        default="用有来源的具体经历回答本期主题",
        min_length=1,
        max_length=500,
    )
    tone: list[ShortText] = Field(
        default_factory=lambda: ["真诚", "克制", "自然口语"],
        min_length=1,
        max_length=3,
    )
    must_include: list[ShortText] = Field(default_factory=list, max_length=10)
    avoid_patterns: list[ShortText] = Field(default_factory=list, max_length=10)

    _normalize_required_fields = field_validator(
        "target_audience",
        "communication_goal",
    )(_normalize_required_text)
    _normalize_unique_lists = field_validator(
        "tone",
        "must_include",
        "avoid_patterns",
    )(_normalize_unique_text_list)


class DraftQualityConfig(BaseModel):
    """Versioned, explicit opt-in policy for post-draft quality review."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    profile: DraftQualityProfile = "podcast_draft_v1"
