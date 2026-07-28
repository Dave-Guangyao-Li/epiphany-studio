from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from epiphany.research_schemas import ThemeResearchOutput, TimelineResearchOutput
from epiphany.schemas import SourceReference

BUILD_INTERVIEW_SCAFFOLD = "build_interview_scaffold"


class InterviewScaffoldOutputError(ValueError):
    code = "interview_scaffold_output_invalid"


class InterviewScaffoldSchemaError(InterviewScaffoldOutputError):
    code = "interview_scaffold_schema_invalid"


class InvalidScaffoldSourceReference(InterviewScaffoldOutputError):
    code = "invalid_scaffold_source_reference"


class ScaffoldTitleTopicMismatch(InterviewScaffoldOutputError):
    code = "interview_scaffold_title_topic_mismatch"


def _normalize_required_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("text must contain non-whitespace characters")
    return normalized


def _unique_references(
    value: list[SourceReference],
) -> list[SourceReference]:
    keys = [(item.source_id, item.source_segment_id) for item in value]
    if len(keys) != len(set(keys)):
        raise ValueError("source_refs must be unique")
    return value


class InterviewScaffoldTaskInput(BaseModel):
    """Trusted, already-validated research artifacts supplied to the Interviewer."""

    model_config = ConfigDict(extra="forbid")

    task_kind: Literal["build_interview_scaffold"]
    topic: str = Field(min_length=1, max_length=200)
    research_bundle_artifact_id: str = Field(min_length=1)
    timeline: TimelineResearchOutput
    themes: ThemeResearchOutput

    @field_validator("topic")
    @classmethod
    def topic_must_not_be_blank(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("topic must contain non-whitespace characters")
        return normalized


class GroundedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2_000)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=10)

    _text_is_not_blank = field_validator("text")(_normalize_required_text)
    _source_refs_are_unique = field_validator("source_refs")(_unique_references)


class InterviewQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=1_000)
    purpose: str = Field(min_length=1, max_length=1_000)
    keywords: list[str] = Field(min_length=1, max_length=8)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=10)

    _text_fields_are_not_blank = field_validator("prompt", "purpose")(_normalize_required_text)
    _source_refs_are_unique = field_validator("source_refs")(_unique_references)

    @field_validator("keywords")
    @classmethod
    def keywords_must_be_non_blank_and_unique(cls, value: list[str]) -> list[str]:
        normalized = [keyword.strip() for keyword in value]
        if any(not keyword for keyword in normalized):
            raise ValueError("keywords must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("keywords must be unique")
        return normalized


class InterviewSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=10)
    known_context: list[GroundedStatement] = Field(min_length=1, max_length=5)
    transition: GroundedStatement
    questions: list[InterviewQuestion] = Field(min_length=1, max_length=6)

    _title_is_not_blank = field_validator("title")(_normalize_required_text)
    _source_refs_are_unique = field_validator("source_refs")(_unique_references)


class MaterialGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap: str = Field(min_length=1, max_length=1_000)
    why_it_matters: str = Field(min_length=1, max_length=1_000)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=10)

    _text_fields_are_not_blank = field_validator("gap", "why_it_matters")(_normalize_required_text)
    _source_refs_are_unique = field_validator("source_refs")(_unique_references)


class InterviewScaffoldOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    episode_intent: GroundedStatement
    opening: GroundedStatement
    sections: list[InterviewSection] = Field(min_length=2, max_length=6)
    material_gaps: list[MaterialGap] = Field(default_factory=list, max_length=10)
    closing: GroundedStatement

    _title_is_not_blank = field_validator("title")(_normalize_required_text)


def collect_research_reference_keys(
    task_input: InterviewScaffoldTaskInput,
) -> set[tuple[str, str]]:
    timeline_references = [
        reference
        for event in task_input.timeline.timeline_events
        for reference in event.source_refs
    ]
    theme_references = [
        reference for theme in task_input.themes.themes for reference in theme.source_refs
    ]
    quote_references = [quote.source_ref for quote in task_input.themes.quotes]
    return {
        (reference.source_id, reference.source_segment_id)
        for reference in timeline_references + theme_references + quote_references
    }


def validate_interview_scaffold_output(
    *,
    task_input: dict[str, Any],
    content: dict[str, Any],
) -> dict[str, Any]:
    try:
        parsed_input = InterviewScaffoldTaskInput.model_validate(task_input)
        parsed_output = InterviewScaffoldOutput.model_validate(content)
    except (ValidationError, ValueError, TypeError) as error:
        raise InterviewScaffoldSchemaError(
            "interview scaffold did not match the strict schema"
        ) from error

    allowed_references = collect_research_reference_keys(parsed_input)
    if parsed_output.title != parsed_input.topic:
        raise ScaffoldTitleTopicMismatch(
            "interview scaffold title must exactly match the requested topic"
        )
    output_references = [
        *parsed_output.episode_intent.source_refs,
        *parsed_output.opening.source_refs,
        *parsed_output.closing.source_refs,
    ]
    output_references.extend(
        reference for section in parsed_output.sections for reference in section.source_refs
    )
    output_references.extend(
        reference
        for section in parsed_output.sections
        for statement in [*section.known_context, section.transition]
        for reference in statement.source_refs
    )
    output_references.extend(
        reference
        for section in parsed_output.sections
        for question in section.questions
        for reference in question.source_refs
    )
    output_references.extend(
        reference for gap in parsed_output.material_gaps for reference in gap.source_refs
    )

    for reference in output_references:
        if (reference.source_id, reference.source_segment_id) not in allowed_references:
            raise InvalidScaffoldSourceReference(
                "interview scaffold cited evidence outside the merged research bundle"
            )

    return parsed_output.model_dump(mode="json")
