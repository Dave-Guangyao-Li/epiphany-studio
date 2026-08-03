from __future__ import annotations

import re
from collections.abc import Iterator
from hashlib import sha256
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from epiphany.interview_schemas import InterviewScaffoldOutput
from epiphany.quality_contract_schemas import CreativeBrief
from epiphany.research_schemas import ResearchSourceSegment
from epiphany.schemas import SourceReference
from epiphany.writing_style import validate_writing_style_profile_segments
from epiphany.writing_style_schemas import (
    MAX_STYLE_SEGMENTS,
    WritingStyleProfile,
    WritingStyleSegmentInput,
)

BUILD_PODCAST_DRAFT = "build_podcast_draft"
MAX_EDITOR_SUPPLEMENTAL_SEGMENTS = 500
SourceReferenceKey = tuple[str, str]
STYLE_SAMPLE_LEAK_WINDOW_CHARS = 24
STYLE_SAMPLE_LEAK_MINIMUM_UNIQUE_CHARS = 8
_STYLE_LEAK_NORMALIZATION_PATTERN = re.compile(r"[\W_]+", re.UNICODE)


class PodcastDraftOutputError(ValueError):
    code = "podcast_draft_output_invalid"


class PodcastDraftSchemaError(PodcastDraftOutputError):
    code = "podcast_draft_schema_invalid"


class InvalidPodcastDraftSourceReference(PodcastDraftOutputError):
    code = "invalid_podcast_draft_source_reference"


class PodcastDraftTitleTopicMismatch(PodcastDraftOutputError):
    code = "podcast_draft_title_topic_mismatch"


class MissingInitialSourceReference(PodcastDraftOutputError):
    code = "podcast_draft_missing_initial_source_reference"


class MissingSupplementalSourceReference(PodcastDraftOutputError):
    code = "podcast_draft_missing_supplemental_source_reference"


class WritingStyleSampleLeak(PodcastDraftOutputError):
    code = "podcast_draft_writing_style_sample_leak"


def _normalize_required_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("text must contain non-whitespace characters")
    return normalized


def _unique_references(value: list[SourceReference]) -> list[SourceReference]:
    keys = [(item.source_id, item.source_segment_id) for item in value]
    if len(keys) != len(set(keys)):
        raise ValueError("source_refs must be unique")
    return value


def _reference_key(reference: SourceReference) -> SourceReferenceKey:
    return reference.source_id, reference.source_segment_id


def _segment_key(segment: ResearchSourceSegment) -> SourceReferenceKey:
    return segment.source_id, segment.source_segment_id


def _iter_scaffold_references(
    scaffold: InterviewScaffoldOutput,
) -> Iterator[SourceReference]:
    yield from scaffold.episode_intent.source_refs
    yield from scaffold.opening.source_refs
    for section in scaffold.sections:
        yield from section.source_refs
        for statement in section.known_context:
            yield from statement.source_refs
        yield from section.transition.source_refs
        for question in section.questions:
            yield from question.source_refs
    for gap in scaffold.material_gaps:
        yield from gap.source_refs
    yield from scaffold.closing.source_refs


class PodcastDraftTaskInput(BaseModel):
    """Trusted workflow artifacts and bounded SourceSegments supplied to the Editor."""

    model_config = ConfigDict(extra="forbid")

    task_kind: Literal["build_podcast_draft"]
    topic: str = Field(min_length=1, max_length=200)
    scaffold_artifact_id: str = Field(min_length=1, max_length=200)
    submission_artifact_id: str = Field(min_length=1, max_length=200)
    submission_artifact_ids: list[str] = Field(default_factory=list)
    creative_brief: CreativeBrief | None = None
    interview_scaffold: InterviewScaffoldOutput
    initial_source_segments: list[ResearchSourceSegment] = Field(
        min_length=1,
    )
    supplemental_source_segments: list[ResearchSourceSegment] = Field(
        min_length=1,
        max_length=MAX_EDITOR_SUPPLEMENTAL_SEGMENTS,
    )
    writing_style_profile: WritingStyleProfile | None = None
    writing_style_segments: list[WritingStyleSegmentInput] | None = Field(
        default=None,
        max_length=MAX_STYLE_SEGMENTS,
    )

    @field_validator("topic")
    @classmethod
    def topic_must_not_be_blank(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("topic must contain non-whitespace characters")
        return normalized

    @model_validator(mode="after")
    def source_segments_must_be_unique_and_cover_scaffold(
        self,
    ) -> PodcastDraftTaskInput:
        if self.submission_artifact_ids:
            if len(self.submission_artifact_ids) != len(set(self.submission_artifact_ids)):
                raise ValueError("submission_artifact_ids must be unique")
            if self.submission_artifact_id not in self.submission_artifact_ids:
                raise ValueError(
                    "submission_artifact_id must be included in submission_artifact_ids"
                )
        initial_keys = [_segment_key(segment) for segment in self.initial_source_segments]
        supplemental_keys = [_segment_key(segment) for segment in self.supplemental_source_segments]
        if len(initial_keys) != len(set(initial_keys)):
            raise ValueError("initial_source_segments must be unique")
        if len(supplemental_keys) != len(set(supplemental_keys)):
            raise ValueError("supplemental_source_segments must be unique")
        if set(initial_keys) & set(supplemental_keys):
            raise ValueError("initial and supplemental source segments must not overlap")

        profile_present = self.writing_style_profile is not None
        style_segments_present = self.writing_style_segments is not None
        if profile_present != style_segments_present:
            raise ValueError(
                "writing_style_profile and writing_style_segments must be supplied together"
            )
        if self.writing_style_profile is not None and self.writing_style_segments is not None:
            validate_writing_style_profile_segments(
                profile=self.writing_style_profile,
                source_segments=self.writing_style_segments,
            )
            style_keys = {
                (segment.source_id, segment.source_segment_id)
                for segment in self.writing_style_segments
            }
            if style_keys & (set(initial_keys) | set(supplemental_keys)):
                raise ValueError("style-only and factual source segments must not overlap")

        initial_key_set = set(initial_keys)
        if any(
            _reference_key(reference) not in initial_key_set
            for reference in _iter_scaffold_references(self.interview_scaffold)
        ):
            raise ValueError(
                "interview scaffold references must resolve to initial source segments"
            )
        return self


class GroundedDraftParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=6_000)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=10)

    _text_is_not_blank = field_validator("text")(_normalize_required_text)
    _source_refs_are_unique = field_validator("source_refs")(_unique_references)


class PodcastDraftSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=10)
    paragraphs: list[GroundedDraftParagraph] = Field(min_length=1, max_length=10)

    _title_is_not_blank = field_validator("title")(_normalize_required_text)
    _source_refs_are_unique = field_validator("source_refs")(_unique_references)


class PodcastScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opening: GroundedDraftParagraph
    sections: list[PodcastDraftSection] = Field(min_length=2, max_length=8)
    closing: GroundedDraftParagraph


class PodcastShowNotes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: GroundedDraftParagraph
    key_points: list[GroundedDraftParagraph] = Field(min_length=2, max_length=8)


class PodcastDraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    podcast_script: PodcastScript
    show_notes: PodcastShowNotes

    _title_is_not_blank = field_validator("title")(_normalize_required_text)


def _iter_script_references(output: PodcastDraftOutput) -> Iterator[SourceReference]:
    yield from output.podcast_script.opening.source_refs
    for section in output.podcast_script.sections:
        yield from section.source_refs
        for paragraph in section.paragraphs:
            yield from paragraph.source_refs
    yield from output.podcast_script.closing.source_refs


def _iter_spoken_script_references(
    output: PodcastDraftOutput,
) -> Iterator[SourceReference]:
    """Yield only references attached to words the listener will hear.

    Section-level references are structural metadata, and Show Notes are a
    separate deliverable. Neither proves that a SourceSegment was actually
    developed in the spoken draft.
    """

    yield from output.podcast_script.opening.source_refs
    for section in output.podcast_script.sections:
        for paragraph in section.paragraphs:
            yield from paragraph.source_refs
    yield from output.podcast_script.closing.source_refs


def _iter_show_notes_references(
    output: PodcastDraftOutput,
) -> Iterator[SourceReference]:
    yield from output.show_notes.summary.source_refs
    for key_point in output.show_notes.key_points:
        yield from key_point.source_refs


def _iter_output_references(output: PodcastDraftOutput) -> Iterator[SourceReference]:
    yield from _iter_script_references(output)
    yield from _iter_show_notes_references(output)


def _iter_output_text(output: PodcastDraftOutput) -> Iterator[str]:
    yield output.podcast_script.opening.text
    for section in output.podcast_script.sections:
        yield section.title
        for paragraph in section.paragraphs:
            yield paragraph.text
    yield output.podcast_script.closing.text
    yield output.show_notes.summary.text
    for key_point in output.show_notes.key_points:
        yield key_point.text


def editor_output_reference_keys(
    content: dict[str, Any],
) -> tuple[SourceReferenceKey, ...]:
    """Return output references in stable first-appearance order."""

    output = PodcastDraftOutput.model_validate(content)
    ordered: list[SourceReferenceKey] = []
    seen: set[SourceReferenceKey] = set()
    for reference in _iter_output_references(output):
        key = _reference_key(reference)
        if key not in seen:
            ordered.append(key)
            seen.add(key)
    return tuple(ordered)


def editor_spoken_script_reference_keys(
    content: dict[str, Any],
) -> tuple[SourceReferenceKey, ...]:
    """Return references used by spoken paragraphs, excluding metadata.

    The stable order is useful when measuring factual-material utilization.
    It intentionally excludes section-level references and Show Notes so a
    citation outside the words to be recorded cannot hide unused material.
    """

    output = PodcastDraftOutput.model_validate(content)
    ordered: list[SourceReferenceKey] = []
    seen: set[SourceReferenceKey] = set()
    for reference in _iter_spoken_script_references(output):
        key = _reference_key(reference)
        if key not in seen:
            ordered.append(key)
            seen.add(key)
    return tuple(ordered)


def validate_podcast_draft_output(
    *,
    task_input: dict[str, Any],
    content: dict[str, Any],
) -> dict[str, Any]:
    try:
        parsed_input = PodcastDraftTaskInput.model_validate(task_input)
        parsed_output = PodcastDraftOutput.model_validate(content)
    except (ValidationError, ValueError, TypeError) as error:
        raise PodcastDraftSchemaError(
            "podcast draft did not match the strict Editor schema"
        ) from error

    if parsed_output.title != parsed_input.topic:
        raise PodcastDraftTitleTopicMismatch(
            "podcast draft title must exactly match the requested topic"
        )

    initial_keys = {_segment_key(segment) for segment in parsed_input.initial_source_segments}
    supplemental_keys = {
        _segment_key(segment) for segment in parsed_input.supplemental_source_segments
    }
    allowed_keys = initial_keys | supplemental_keys

    output_keys = {
        _reference_key(reference) for reference in _iter_output_references(parsed_output)
    }
    if not output_keys <= allowed_keys:
        raise InvalidPodcastDraftSourceReference(
            "podcast draft cited a source segment outside the Editor task scope"
        )

    script_keys = {
        _reference_key(reference) for reference in _iter_script_references(parsed_output)
    }
    if not script_keys & initial_keys:
        raise MissingInitialSourceReference(
            "podcast script must retain at least one initial source reference"
        )
    if not script_keys & supplemental_keys:
        raise MissingSupplementalSourceReference(
            "podcast script must use at least one supplemental source reference"
        )

    show_notes_keys = {
        _reference_key(reference) for reference in _iter_show_notes_references(parsed_output)
    }
    if not show_notes_keys & supplemental_keys:
        raise MissingSupplementalSourceReference(
            "show notes must use at least one supplemental source reference"
        )

    if parsed_input.writing_style_segments is not None:
        factual_texts = [
            segment.text
            for segment in [
                *parsed_input.initial_source_segments,
                *parsed_input.supplemental_source_segments,
            ]
        ]
        if _contains_unique_style_sample_window(
            output_texts=list(_iter_output_text(parsed_output)),
            style_texts=[segment.text for segment in parsed_input.writing_style_segments],
            factual_texts=factual_texts,
        ):
            raise WritingStyleSampleLeak(
                "podcast draft copied a distinctive long passage from a style-only sample"
            )

    return parsed_output.model_dump(mode="json")


def _contains_unique_style_sample_window(
    *,
    output_texts: list[str],
    style_texts: list[str],
    factual_texts: list[str],
) -> bool:
    output_windows = {
        window
        for text in output_texts
        for window in _distinctive_windows(_normalize_style_leak_text(text))
    }
    if not output_windows:
        return False
    factual_windows = {
        window
        for text in factual_texts
        for window in _distinctive_windows(_normalize_style_leak_text(text))
    }
    for text in style_texts:
        for window in _distinctive_windows(_normalize_style_leak_text(text)):
            if window in output_windows and window not in factual_windows:
                return True
    return False


def _normalize_style_leak_text(text: str) -> str:
    return _STYLE_LEAK_NORMALIZATION_PATTERN.sub("", text).casefold()


def _distinctive_windows(text: str) -> Iterator[str]:
    for start in range(0, len(text) - STYLE_SAMPLE_LEAK_WINDOW_CHARS + 1):
        window = text[start : start + STYLE_SAMPLE_LEAK_WINDOW_CHARS]
        if len(set(window)) >= STYLE_SAMPLE_LEAK_MINIMUM_UNIQUE_CHARS:
            yield sha256(window.encode("utf-8")).hexdigest()
