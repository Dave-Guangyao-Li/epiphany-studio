from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from epiphany.schemas import SourceReference

TIMELINE_RESEARCH = "timeline_research"
THEME_RESEARCH = "theme_research"


class ResearchOutputError(ValueError):
    code = "research_output_invalid"


class ResearchSchemaError(ResearchOutputError):
    code = "research_schema_invalid"


class InvalidSourceReference(ResearchOutputError):
    code = "invalid_source_reference"


class QuoteSourceMismatch(ResearchOutputError):
    code = "quote_source_mismatch"


class EpisodeResearchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("topic")
    @classmethod
    def topic_must_not_be_blank(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("topic must contain non-whitespace characters")
        return normalized

    @field_validator("source_ids")
    @classmethod
    def source_ids_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("source_ids must be unique")
        return value


class ResearchSourceSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_segment_id: str
    text: str = Field(min_length=1)


class TimelineEventCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    time_expression: str | None = Field(default=None, max_length=200)
    confidence: float = Field(ge=0, le=1)
    source_refs: list[SourceReference] = Field(min_length=1)


class TimelineResearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeline_events: list[TimelineEventCandidate] = Field(min_length=1, max_length=50)
    open_questions: list[str] = Field(default_factory=list, max_length=20)


class ThemeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str = Field(min_length=1, max_length=200)
    insight: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    source_refs: list[SourceReference] = Field(min_length=1)


class QuoteCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: str = Field(min_length=1, max_length=2_000)
    context: str | None = Field(default=None, max_length=500)
    source_ref: SourceReference


class ThemeResearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    themes: list[ThemeCandidate] = Field(min_length=1, max_length=30)
    quotes: list[QuoteCandidate] = Field(default_factory=list, max_length=30)


ResearchTaskKind = Literal["timeline_research", "theme_research"]


def validate_research_output(
    *,
    task_kind: str,
    task_input: dict[str, Any],
    content: dict[str, Any],
) -> dict[str, Any]:
    if task_kind not in {TIMELINE_RESEARCH, THEME_RESEARCH}:
        return content

    try:
        segments = [
            ResearchSourceSegment.model_validate(item)
            for item in task_input.get("source_segments", [])
        ]
        if not segments:
            raise ValueError("research task has no source segments")
        if task_kind == TIMELINE_RESEARCH:
            parsed: TimelineResearchOutput | ThemeResearchOutput = (
                TimelineResearchOutput.model_validate(content)
            )
        else:
            parsed = ThemeResearchOutput.model_validate(content)
    except (ValidationError, ValueError, TypeError) as error:
        raise ResearchSchemaError("research output did not match the strict schema") from error

    allowed_segments = {
        (segment.source_id, segment.source_segment_id): segment.text for segment in segments
    }

    if isinstance(parsed, TimelineResearchOutput):
        references = [
            reference for event in parsed.timeline_events for reference in event.source_refs
        ]
    else:
        references = [reference for theme in parsed.themes for reference in theme.source_refs] + [
            quote.source_ref for quote in parsed.quotes
        ]

    for reference in references:
        key = (reference.source_id, reference.source_segment_id)
        if key not in allowed_segments:
            raise InvalidSourceReference(
                "research output cited a source segment outside the task scope"
            )

    if isinstance(parsed, ThemeResearchOutput):
        for quote in parsed.quotes:
            source_text = allowed_segments[
                (quote.source_ref.source_id, quote.source_ref.source_segment_id)
            ]
            if quote.quote not in source_text:
                raise QuoteSourceMismatch(
                    "quoted text was not found in the referenced source segment"
                )

    return parsed.model_dump(mode="json")
