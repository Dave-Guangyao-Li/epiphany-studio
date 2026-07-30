from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from epiphany.editor_schemas import PodcastDraftOutput
from epiphany.quality_contract_schemas import (
    CreativeBrief,
    DraftQualityConfig,
    DraftQualityProfile,
)
from epiphany.schemas import ArtifactView, SourceReference

REVIEW_PODCAST_DRAFT = "review_podcast_draft"
DRAFT_QUALITY_FORMULA_VERSION = "draft_quality_v1_60_40"
_INTERNAL_SOURCE_IDENTIFIER = re.compile(r"(?:src|seg)_[A-Za-z0-9][A-Za-z0-9_-]*")

FindingStatus = Literal["pass", "warning", "blocker"]
DraftQualityDecision = Literal[
    "blocked",
    "automated_review_incomplete",
    "revision_recommended",
    "candidate_ready_for_human_review",
]
ReviewerRelation = Literal["same_model", "different_model", "unknown"]
ReviewDimensionName = Literal[
    "brief_adherence",
    "source_faithfulness",
    "coverage_and_specificity",
    "structure_and_coherence",
    "oral_naturalness_and_voice_fit",
    "conciseness_and_non_redundancy",
]

REVIEW_DIMENSIONS: tuple[ReviewDimensionName, ...] = (
    "brief_adherence",
    "source_faithfulness",
    "coverage_and_specificity",
    "structure_and_coherence",
    "oral_naturalness_and_voice_fit",
    "conciseness_and_non_redundancy",
)

ObservedValue = int | float | str | bool
RequiredText = Annotated[str, Field(min_length=1, max_length=2_000)]


def _normalize_required_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("text must contain non-whitespace characters")
    return normalized


def _reference_key(reference: SourceReference) -> tuple[str, str]:
    return reference.source_id, reference.source_segment_id


def _unique_references(value: list[SourceReference]) -> list[SourceReference]:
    keys = [_reference_key(reference) for reference in value]
    if len(keys) != len(set(keys)):
        raise ValueError("source_refs must be unique")
    return value


def _iter_draft_references(draft: PodcastDraftOutput) -> Iterator[SourceReference]:
    yield from draft.podcast_script.opening.source_refs
    for section in draft.podcast_script.sections:
        yield from section.source_refs
        for paragraph in section.paragraphs:
            yield from paragraph.source_refs
    yield from draft.podcast_script.closing.source_refs
    yield from draft.show_notes.summary.source_refs
    for key_point in draft.show_notes.key_points:
        yield from key_point.source_refs


def podcast_draft_text_blocks(draft: PodcastDraftOutput) -> dict[str, str]:
    """Return stable, public evidence locations for the strict draft."""

    blocks = {
        "title": draft.title,
        "podcast_script.opening": draft.podcast_script.opening.text,
        "podcast_script.closing": draft.podcast_script.closing.text,
        "show_notes.summary": draft.show_notes.summary.text,
    }
    for section_index, section in enumerate(draft.podcast_script.sections):
        blocks[f"podcast_script.sections[{section_index}].title"] = section.title
        for paragraph_index, paragraph in enumerate(section.paragraphs):
            blocks[f"podcast_script.sections[{section_index}].paragraphs[{paragraph_index}]"] = (
                paragraph.text
            )
    for point_index, point in enumerate(draft.show_notes.key_points):
        blocks[f"show_notes.key_points[{point_index}]"] = point.text
    return blocks


def podcast_draft_reference_blocks(
    draft: PodcastDraftOutput,
) -> dict[str, set[tuple[str, str]]]:
    all_references = {_reference_key(reference) for reference in _iter_draft_references(draft)}
    blocks = {
        "title": all_references,
        "podcast_script.opening": {
            _reference_key(reference) for reference in draft.podcast_script.opening.source_refs
        },
        "podcast_script.closing": {
            _reference_key(reference) for reference in draft.podcast_script.closing.source_refs
        },
        "show_notes.summary": {
            _reference_key(reference) for reference in draft.show_notes.summary.source_refs
        },
    }
    for section_index, section in enumerate(draft.podcast_script.sections):
        blocks[f"podcast_script.sections[{section_index}].title"] = {
            _reference_key(reference) for reference in section.source_refs
        }
        for paragraph_index, paragraph in enumerate(section.paragraphs):
            blocks[f"podcast_script.sections[{section_index}].paragraphs[{paragraph_index}]"] = {
                _reference_key(reference) for reference in paragraph.source_refs
            }
    for point_index, point in enumerate(draft.show_notes.key_points):
        blocks[f"show_notes.key_points[{point_index}]"] = {
            _reference_key(reference) for reference in point.source_refs
        }
    return blocks


class DraftQualityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    status: FindingStatus
    location: str = Field(min_length=1, max_length=500)
    exact_quote: str = Field(default="", max_length=500)
    observed: ObservedValue
    threshold: ObservedValue

    _normalize_code_and_location = field_validator("code", "location")(_normalize_required_text)


class DeterministicDraftMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_duration_minutes: int
    estimated_duration_minutes: float
    speaking_rate_chars_per_minute: int
    script_character_count: int
    paragraph_count: int
    cited_paragraph_count: int
    paragraph_citation_coverage: float = Field(ge=0, le=1)
    unique_source_count: int
    unique_segment_count: int
    exact_duplicate_paragraph_count: int
    repeated_eight_character_window_ratio: float = Field(ge=0, le=1)
    must_include_missing_count: int
    avoid_pattern_hit_count: int
    filler_phrase_count: int
    filler_phrase_density_per_1000_chars: float = Field(ge=0)
    template_phrase_count: int
    not_but_pattern_count: int


class DeterministicDraftQualityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: DraftQualityProfile
    deterministic_score: int = Field(ge=0, le=100)
    metrics: DeterministicDraftMetrics
    findings: list[DraftQualityFinding]

    @property
    def has_blocker(self) -> bool:
        return any(finding.status == "blocker" for finding in self.findings)

    @property
    def has_warning(self) -> bool:
        return any(finding.status == "warning" for finding in self.findings)


class ReviewSourceSegment(BaseModel):
    """One source segment actually cited by the Draft and exposed to Reviewer."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=200)
    source_segment_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)


class ModelSelfReviewTaskInput(BaseModel):
    """Trusted task contract for a separate, advisory model review."""

    model_config = ConfigDict(extra="forbid")

    task_kind: Literal["review_podcast_draft"]
    draft_artifact_id: str = Field(min_length=1, max_length=200)
    creative_brief: CreativeBrief
    quality_config: DraftQualityConfig = Field(default_factory=DraftQualityConfig)
    podcast_draft: PodcastDraftOutput
    allowed_source_refs: list[SourceReference] = Field(min_length=1, max_length=500)
    referenced_source_segments: list[ReviewSourceSegment] = Field(
        min_length=1,
        max_length=500,
    )

    _source_refs_are_unique = field_validator("allowed_source_refs")(_unique_references)

    @model_validator(mode="after")
    def draft_references_must_be_allowed(self) -> ModelSelfReviewTaskInput:
        if not self.quality_config.enabled:
            raise ValueError("quality review task cannot use a disabled quality config")
        allowed = {_reference_key(reference) for reference in self.allowed_source_refs}
        if any(
            _reference_key(reference) not in allowed
            for reference in _iter_draft_references(self.podcast_draft)
        ):
            raise ValueError("podcast draft source_refs must be within allowed_source_refs")
        segment_keys = {
            (segment.source_id, segment.source_segment_id)
            for segment in self.referenced_source_segments
        }
        if segment_keys != allowed:
            raise ValueError("referenced_source_segments must exactly match allowed_source_refs")
        return self


class ModelReviewEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: str = Field(min_length=1, max_length=500)
    exact_quote: str = Field(min_length=1, max_length=500)
    source_refs: list[SourceReference] = Field(default_factory=list, max_length=10)

    _text_is_not_blank = field_validator("location", "exact_quote")(_normalize_required_text)
    _source_refs_are_unique = field_validator("source_refs")(_unique_references)


class ModelReviewDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: ReviewDimensionName
    assessable: bool
    score: int | None = Field(default=None, ge=1, le=5)
    assessment: RequiredText
    limitation: str | None = Field(default=None, min_length=1, max_length=2_000)
    evidence: list[ModelReviewEvidence] = Field(default_factory=list, max_length=5)

    _assessment_is_not_blank = field_validator("assessment")(_normalize_required_text)

    @field_validator("limitation")
    @classmethod
    def limitation_must_not_be_blank(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_required_text(value)

    @model_validator(mode="after")
    def assessment_state_must_be_consistent(self) -> ModelReviewDimension:
        if self.assessable:
            if self.score is None:
                raise ValueError("assessable dimensions require a score")
            if not self.evidence:
                raise ValueError("assessable dimensions require verbatim evidence")
        else:
            if self.score is not None:
                raise ValueError("unassessable dimensions must not provide a score")
            if self.evidence:
                raise ValueError("unassessable dimensions must not invent evidence")
            if self.limitation is None:
                raise ValueError("unassessable dimensions require a limitation")
        return self


class ModelSelfReviewOutput(BaseModel):
    """Strict model output.

    Scores are evidence-bearing dimension cards, not an objective truth and not
    an "AI probability". The aggregate is calculated later by application code.
    """

    model_config = ConfigDict(extra="forbid")

    review_kind: Literal["model_self_review"] = "model_self_review"
    advisory: Literal[True] = True
    dimensions: list[ModelReviewDimension] = Field(
        min_length=len(REVIEW_DIMENSIONS),
        max_length=len(REVIEW_DIMENSIONS),
    )

    @model_validator(mode="after")
    def dimensions_must_be_fixed_and_unique(self) -> ModelSelfReviewOutput:
        names = [dimension.dimension for dimension in self.dimensions]
        if len(names) != len(set(names)):
            raise ValueError("review dimensions must be unique")
        if set(names) != set(REVIEW_DIMENSIONS):
            raise ValueError("review must contain every fixed dimension exactly once")
        return self


class ModelSelfReviewOutputError(ValueError):
    code = "model_self_review_output_invalid"


class ModelSelfReviewSchemaError(ModelSelfReviewOutputError):
    code = "model_self_review_schema_invalid"


class InvalidModelReviewEvidence(ModelSelfReviewOutputError):
    code = "invalid_model_review_evidence"


class InvalidModelReviewSourceReference(ModelSelfReviewOutputError):
    code = "invalid_model_review_source_reference"


def validate_model_self_review_output(
    *,
    task_input: dict[str, Any],
    content: dict[str, Any],
) -> dict[str, Any]:
    """Validate model review structure, verbatim evidence, and reference scope."""

    try:
        parsed_input = ModelSelfReviewTaskInput.model_validate(task_input)
        parsed_output = ModelSelfReviewOutput.model_validate(content)
    except (ValidationError, ValueError, TypeError) as error:
        raise ModelSelfReviewSchemaError(
            "model self-review did not match the strict schema"
        ) from error

    blocks = podcast_draft_text_blocks(parsed_input.podcast_draft)
    block_references = podcast_draft_reference_blocks(parsed_input.podcast_draft)
    allowed_references = {
        _reference_key(reference) for reference in parsed_input.allowed_source_refs
    }
    for dimension in parsed_output.dimensions:
        narrative_fields = [
            dimension.assessment,
            dimension.limitation or "",
            *[evidence.exact_quote for evidence in dimension.evidence],
        ]
        if any(_INTERNAL_SOURCE_IDENTIFIER.search(value) for value in narrative_fields):
            raise InvalidModelReviewEvidence(
                "review prose must not expose internal Source or Segment identifiers"
            )
        for evidence in dimension.evidence:
            block_text = blocks.get(evidence.location)
            if block_text is None or evidence.exact_quote not in block_text:
                raise InvalidModelReviewEvidence(
                    "review evidence must be a verbatim quote from its draft block"
                )
            if any(
                _reference_key(reference) not in allowed_references
                for reference in evidence.source_refs
            ):
                raise InvalidModelReviewSourceReference(
                    "review evidence source_refs must stay within the task scope"
                )
            if any(
                _reference_key(reference) not in block_references[evidence.location]
                for reference in evidence.source_refs
            ):
                raise InvalidModelReviewSourceReference(
                    "review evidence source_refs must be attached to its Draft block"
                )

    faithfulness = next(
        dimension
        for dimension in parsed_output.dimensions
        if dimension.dimension == "source_faithfulness"
    )
    if faithfulness.assessable and not any(
        evidence.source_refs for evidence in faithfulness.evidence
    ):
        raise InvalidModelReviewSourceReference(
            "assessable source_faithfulness requires referenced evidence"
        )

    return parsed_output.model_dump(mode="json")


class DraftQualityReport(BaseModel):
    """Code-owned synthesis of deterministic checks and advisory model review."""

    model_config = ConfigDict(extra="forbid")

    profile: DraftQualityProfile
    deterministic: DeterministicDraftQualityResult
    model_self_review: ModelSelfReviewOutput | None
    model_review_status: Literal["completed", "unavailable"]
    model_review_unavailable_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    model_review_advisory: Literal[True] = True
    reviewer_relation: ReviewerRelation | None
    scoring_formula_version: Literal["draft_quality_v1_60_40"] = DRAFT_QUALITY_FORMULA_VERSION
    deterministic_weight: Literal[0.6] = 0.6
    model_weight: Literal[0.4] = 0.4
    experimental_model_score: float | None = Field(default=None, ge=0, le=100)
    experimental_overall_score: float | None = Field(default=None, ge=0, le=100)
    decision: DraftQualityDecision
    requires_human_review: Literal[True] = True


class DraftQualityReportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: DraftQualityReport
    artifact: ArtifactView
