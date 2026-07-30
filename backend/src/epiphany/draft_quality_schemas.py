from __future__ import annotations

import re
from collections.abc import Iterator
from hashlib import sha256
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
from epiphany.writing_style_schemas import (
    MAX_STYLE_NON_WHITESPACE_CHARS,
    WritingStyleProfile,
    WritingStyleSegmentInput,
)

REVIEW_PODCAST_DRAFT = "review_podcast_draft"
LEGACY_MODEL_REVIEW_TASK_VERSION = "model_self_review_task_v1"
MODEL_REVIEW_TASK_VERSION = "model_self_review_task_v2_deterministic_facts"
STYLE_AWARE_MODEL_REVIEW_TASK_VERSION = "model_self_review_task_v3_writing_style"
LEGACY_DRAFT_QUALITY_FORMULA_VERSION = "draft_quality_v1_60_40"
DRAFT_QUALITY_FORMULA_VERSION = "draft_quality_v2_non_compensatory_caps"
STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION = "draft_quality_v3_personal_style_non_compensatory_caps"
DETERMINISTIC_QUALITY_FACTS_VERSION = "deterministic_quality_facts_v1"
LEGACY_DRAFT_QUALITY_RULES_VERSION = "draft_quality_rules_v1"
DRAFT_QUALITY_RULES_VERSION = "draft_quality_rules_v2_chinese_calibration"
CHINESE_STYLE_HEURISTIC_VERSION = "zh_podcast_style_v1"
_INTERNAL_SOURCE_IDENTIFIER = re.compile(r"(?:src|seg)_[A-Za-z0-9][A-Za-z0-9_-]*")
_UNSUPPORTED_PERSONAL_STYLE_CLAIM = re.compile(
    r"(?:很|较|更|非常|确实|明显|高度)?像(?:作者|用户)?本人|"
    r"(?:符合|贴合|还原)(?:作者|用户|本人)的?(?:个人)?(?:写作|表达)?风格"
)

FindingStatus = Literal["pass", "info", "warning", "blocker"]
DraftQualityDecision = Literal[
    "blocked",
    "automated_review_incomplete",
    "revision_recommended",
    "candidate_ready_for_human_review",
]
ReviewerRelation = Literal[
    "same_model",
    "cross_tier_same_family",
    "different_model",
    "unknown",
]
ReviewDimensionName = Literal[
    "brief_adherence",
    "source_faithfulness",
    "coverage_and_specificity",
    "structure_and_coherence",
    "oral_naturalness_and_voice_fit",
    "conciseness_and_non_redundancy",
    "personal_style_match",
]
WritingStyleContextStatus = Literal["not_provided", "limited", "ready"]

REVIEW_DIMENSIONS: tuple[ReviewDimensionName, ...] = (
    "brief_adherence",
    "source_faithfulness",
    "coverage_and_specificity",
    "structure_and_coherence",
    "oral_naturalness_and_voice_fit",
    "conciseness_and_non_redundancy",
)
PERSONAL_STYLE_DIMENSION: ReviewDimensionName = "personal_style_match"
STYLE_AWARE_REVIEW_DIMENSIONS: tuple[ReviewDimensionName, ...] = (
    *REVIEW_DIMENSIONS,
    PERSONAL_STYLE_DIMENSION,
)
PERSONAL_STYLE_MODEL_WEIGHT = 0.30

ObservedValue = int | float | str | bool
RequiredText = Annotated[str, Field(min_length=1, max_length=2_000)]


def _normalize_required_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("text must contain non-whitespace characters")
    return normalized


def _normalize_unique_required_texts(value: list[str]) -> list[str]:
    normalized = [_normalize_required_text(item) for item in value]
    if len(normalized) != len(set(normalized)):
        raise ValueError("text items must be unique")
    return normalized


def _reference_key(reference: SourceReference) -> tuple[str, str]:
    return reference.source_id, reference.source_segment_id


def _unique_references(value: list[SourceReference]) -> list[SourceReference]:
    keys = [_reference_key(reference) for reference in value]
    if len(keys) != len(set(keys)):
        raise ValueError("source_refs must be unique")
    return value


def expected_review_dimensions(
    writing_style_context_status: WritingStyleContextStatus,
) -> tuple[ReviewDimensionName, ...]:
    return (
        STYLE_AWARE_REVIEW_DIMENSIONS
        if writing_style_context_status == "ready"
        else REVIEW_DIMENSIONS
    )


def calculate_model_review_score(
    review: ModelSelfReviewOutput,
    *,
    scoring_formula_version: str,
    writing_style_context_status: WritingStyleContextStatus,
) -> float | None:
    """Calculate an experimental score without weakening deterministic caps."""

    expected_dimensions = expected_review_dimensions(writing_style_context_status)
    cards = {dimension.dimension: dimension for dimension in review.dimensions}
    if set(cards) != set(expected_dimensions):
        return None
    scores = {
        name: cards[name].score
        for name in expected_dimensions
        if cards[name].assessable and cards[name].score is not None
    }
    if len(scores) != len(expected_dimensions):
        return None

    base_score = sum(scores[name] for name in REVIEW_DIMENSIONS) / len(REVIEW_DIMENSIONS) / 5 * 100
    if (
        scoring_formula_version == STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION
        and writing_style_context_status == "ready"
    ):
        style_score = scores[PERSONAL_STYLE_DIMENSION] / 5 * 100
        return round(
            (1 - PERSONAL_STYLE_MODEL_WEIGHT) * base_score
            + PERSONAL_STYLE_MODEL_WEIGHT * style_score,
            2,
        )
    return round(base_score, 2)


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


class ChineseStylePatternCounts(BaseModel):
    """Versioned, observable Chinese style signals.

    These counts describe wording in the spoken script. They are not an
    authorship classifier and must not be presented as an "AI probability".
    Defaults keep persisted reports from before this profile readable.
    """

    model_config = ConfigDict(extra="forbid")

    parallel_contrast: int = Field(default=0, ge=0)
    escalation: int = Field(default=0, ge=0)
    enumeration: int = Field(default=0, ge=0)
    generic_transition: int = Field(default=0, ge=0)
    generic_epiphany: int = Field(default=0, ge=0)
    generic_coda: int = Field(default=0, ge=0)
    over_polite: int = Field(default=0, ge=0)


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
    rules_version: Literal[
        "draft_quality_rules_v1",
        "draft_quality_rules_v2_chinese_calibration",
    ] = LEGACY_DRAFT_QUALITY_RULES_VERSION
    chinese_style_heuristic_version: Literal["zh_podcast_style_v1"] | None = None
    chinese_style_pattern_counts: ChineseStylePatternCounts = Field(
        default_factory=ChineseStylePatternCounts
    )
    spoken_sentence_count: int = Field(default=0, ge=0)
    spoken_nonempty_paragraph_count: int = Field(default=0, ge=0)
    sentence_length_cv: float | None = Field(default=None, ge=0)
    paragraph_length_cv: float | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def infer_pre_versioned_rules(cls, value: Any) -> Any:
        """Distinguish historical M3.4 metrics from pre-release M3.5 metrics."""

        if not isinstance(value, dict) or "rules_version" in value:
            return value
        normalized = dict(value)
        normalized["rules_version"] = (
            DRAFT_QUALITY_RULES_VERSION
            if normalized.get("chinese_style_heuristic_version") == CHINESE_STYLE_HEURISTIC_VERSION
            else LEGACY_DRAFT_QUALITY_RULES_VERSION
        )
        return normalized


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


class DeterministicQualityFacts(BaseModel):
    """Small, code-owned Reviewer bundle derived from the persisted metrics Artifact.

    A caller cannot submit this bundle through the public Run API. The
    Orchestrator constructs it only after loading the deterministic Artifact
    associated with the exact Draft under review.
    """

    model_config = ConfigDict(extra="forbid")

    facts_version: Literal["deterministic_quality_facts_v1"] = DETERMINISTIC_QUALITY_FACTS_VERSION
    rules_version: Literal["draft_quality_rules_v2_chinese_calibration"] = (
        DRAFT_QUALITY_RULES_VERSION
    )
    quality_profile: DraftQualityProfile
    deterministic_score: int = Field(ge=0, le=100)
    target_duration_minutes: int = Field(gt=0)
    script_character_count: int = Field(ge=0)
    estimated_duration_minutes: float = Field(ge=0)
    duration_coverage_ratio: float = Field(ge=0)
    duration_status: FindingStatus
    duration_finding_code: str = Field(min_length=1, max_length=100)
    paragraph_citation_coverage: float = Field(ge=0, le=1)
    blocker_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    chinese_style_heuristic_version: Literal["zh_podcast_style_v1"] = (
        CHINESE_STYLE_HEURISTIC_VERSION
    )
    chinese_style_pattern_counts: ChineseStylePatternCounts = Field(
        default_factory=ChineseStylePatternCounts
    )
    filler_phrase_count: int = Field(ge=0)
    template_phrase_count: int = Field(ge=0)
    not_but_pattern_count: int = Field(ge=0)

    _normalize_duration_code = field_validator("duration_finding_code")(_normalize_required_text)


class ReviewSourceSegment(BaseModel):
    """One source segment actually cited by the Draft and exposed to Reviewer."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=200)
    source_segment_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)


class ModelSelfReviewTaskInput(BaseModel):
    """Trusted task contract for a separate, advisory model review."""

    model_config = ConfigDict(extra="forbid")

    review_contract_version: Literal[
        "model_self_review_task_v1",
        "model_self_review_task_v2_deterministic_facts",
        "model_self_review_task_v3_writing_style",
    ]
    task_kind: Literal["review_podcast_draft"]
    draft_artifact_id: str = Field(min_length=1, max_length=200)
    deterministic_metrics_artifact_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    deterministic_quality_facts: DeterministicQualityFacts | None = None
    creative_brief: CreativeBrief
    quality_config: DraftQualityConfig = Field(default_factory=DraftQualityConfig)
    podcast_draft: PodcastDraftOutput
    allowed_source_refs: list[SourceReference] = Field(min_length=1, max_length=500)
    referenced_source_segments: list[ReviewSourceSegment] = Field(
        min_length=1,
        max_length=500,
    )
    writing_style_profile: WritingStyleProfile | None = None
    writing_style_segments: list[WritingStyleSegmentInput] = Field(
        default_factory=list,
        max_length=20,
    )

    _source_refs_are_unique = field_validator("allowed_source_refs")(_unique_references)

    @model_validator(mode="before")
    @classmethod
    def infer_persisted_contract_version(cls, value: Any) -> Any:
        """Read both pre-M3.5 v1 tasks and the explicit v2 task contract.

        M3.4 persisted Reviewer Tasks before the deterministic facts fields
        existed. They may still be queued or leased when a process restarts
        after M3.5 is deployed, so absence of all new fields is a deliberate
        legacy signal rather than an invalid payload.
        """

        if not isinstance(value, dict) or "review_contract_version" in value:
            return value
        normalized = dict(value)
        has_style_context = normalized.get("writing_style_profile") is not None or bool(
            normalized.get("writing_style_segments")
        )
        has_metrics_id = normalized.get("deterministic_metrics_artifact_id") is not None
        has_facts = normalized.get("deterministic_quality_facts") is not None
        normalized["review_contract_version"] = (
            STYLE_AWARE_MODEL_REVIEW_TASK_VERSION
            if has_style_context
            else MODEL_REVIEW_TASK_VERSION
            if has_metrics_id or has_facts
            else LEGACY_MODEL_REVIEW_TASK_VERSION
        )
        return normalized

    @model_validator(mode="after")
    def draft_references_must_be_allowed(self) -> ModelSelfReviewTaskInput:
        if not self.quality_config.enabled:
            raise ValueError("quality review task cannot use a disabled quality config")
        if self.review_contract_version == LEGACY_MODEL_REVIEW_TASK_VERSION:
            if (
                self.deterministic_metrics_artifact_id is not None
                or self.deterministic_quality_facts is not None
            ):
                raise ValueError("legacy review tasks cannot contain deterministic facts")
        else:
            if (
                self.deterministic_metrics_artifact_id is None
                or self.deterministic_quality_facts is None
            ):
                raise ValueError("current review tasks require deterministic facts")
            facts = self.deterministic_quality_facts
            if facts.quality_profile != self.quality_config.profile:
                raise ValueError("deterministic facts must use the configured quality profile")
            if facts.target_duration_minutes != self.creative_brief.target_duration_minutes:
                raise ValueError("deterministic facts must use the Creative Brief duration")
            spoken_texts = [
                self.podcast_draft.podcast_script.opening.text,
                *[
                    paragraph.text
                    for section in self.podcast_draft.podcast_script.sections
                    for paragraph in section.paragraphs
                ],
                self.podcast_draft.podcast_script.closing.text,
            ]
            script_character_count = len("".join("".join(text.split()) for text in spoken_texts))
            if facts.script_character_count != script_character_count:
                raise ValueError("deterministic facts must describe the exact Draft spoken text")
            expected_minutes = round(
                script_character_count / self.creative_brief.speaking_rate_chars_per_minute,
                2,
            )
            if facts.estimated_duration_minutes != expected_minutes:
                raise ValueError("deterministic facts contain an inconsistent duration estimate")
            expected_coverage = round(
                expected_minutes / self.creative_brief.target_duration_minutes,
                4,
            )
            if facts.duration_coverage_ratio != expected_coverage:
                raise ValueError("deterministic facts contain an inconsistent duration coverage")
            script_blocks = [
                self.podcast_draft.podcast_script.opening,
                *[
                    paragraph
                    for section in self.podcast_draft.podcast_script.sections
                    for paragraph in section.paragraphs
                ],
                self.podcast_draft.podcast_script.closing,
            ]
            expected_citation_coverage = round(
                sum(bool(block.source_refs) for block in script_blocks) / len(script_blocks),
                4,
            )
            if facts.paragraph_citation_coverage != expected_citation_coverage:
                raise ValueError("deterministic facts contain inconsistent citation coverage")
        if self.review_contract_version != STYLE_AWARE_MODEL_REVIEW_TASK_VERSION:
            if self.writing_style_profile is not None or self.writing_style_segments:
                raise ValueError("only v3 review tasks may contain writing style context")
        else:
            self._validate_writing_style_context()
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

    @property
    def writing_style_context_status(self) -> WritingStyleContextStatus:
        if self.writing_style_profile is None:
            return "not_provided"
        return self.writing_style_profile.readiness.status

    def _validate_writing_style_context(self) -> None:
        if self.writing_style_profile is None:
            if self.writing_style_segments:
                raise ValueError("writing style segments require a writing style profile")
            return

        profile = self.writing_style_profile
        expected = {
            (segment.source_id, segment.source_segment_id): segment
            for segment in profile.selected_segments
        }
        actual = {
            (segment.source_id, segment.source_segment_id): segment
            for segment in self.writing_style_segments
        }
        if len(actual) != len(self.writing_style_segments):
            raise ValueError("writing style segments must be unique")
        if set(actual) != set(expected):
            raise ValueError("writing style segments must exactly cover profile-selected segments")
        total_characters = 0
        for key, segment in actual.items():
            reference = expected[key]
            non_whitespace_characters = len("".join(segment.text.split()))
            total_characters += non_whitespace_characters
            if (
                segment.position != reference.position
                or sha256(segment.text.encode("utf-8")).hexdigest() != reference.content_sha256
                or non_whitespace_characters != reference.non_whitespace_char_count
            ):
                raise ValueError("writing style segment text does not match its profile provenance")
        if total_characters > MAX_STYLE_NON_WHITESPACE_CHARS:
            raise ValueError("writing style context exceeds the character limit")


class ModelReviewEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: str = Field(min_length=1, max_length=500)
    exact_quote: str = Field(min_length=1, max_length=500)
    source_refs: list[SourceReference] = Field(default_factory=list, max_length=10)

    _text_is_not_blank = field_validator("location", "exact_quote")(_normalize_required_text)
    _source_refs_are_unique = field_validator("source_refs")(_unique_references)


class ModelReviewStyleEvidence(BaseModel):
    """Verbatim style-only evidence, never episode factual grounding."""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(min_length=1, max_length=500)
    exact_quote: str = Field(min_length=1, max_length=500)
    source_ref: SourceReference

    _text_is_not_blank = field_validator("location", "exact_quote")(_normalize_required_text)


class ModelReviewDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: ReviewDimensionName
    assessable: bool
    score: int | None = Field(default=None, ge=1, le=5)
    assessment: RequiredText
    limitation: str | None = Field(default=None, min_length=1, max_length=2_000)
    evidence: list[ModelReviewEvidence] = Field(default_factory=list, max_length=5)
    style_sample_evidence: list[ModelReviewStyleEvidence] = Field(
        default_factory=list,
        max_length=5,
    )

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
            if self.style_sample_evidence:
                raise ValueError("unassessable dimensions must not invent style evidence")
            if self.limitation is None:
                raise ValueError("unassessable dimensions require a limitation")
        if self.dimension != PERSONAL_STYLE_DIMENSION and self.style_sample_evidence:
            raise ValueError("only personal_style_match may contain style sample evidence")
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
        max_length=len(STYLE_AWARE_REVIEW_DIMENSIONS),
    )

    @model_validator(mode="after")
    def dimensions_must_be_fixed_and_unique(self) -> ModelSelfReviewOutput:
        names = [dimension.dimension for dimension in self.dimensions]
        if len(names) != len(set(names)):
            raise ValueError("review dimensions must be unique")
        if frozenset(names) not in {
            frozenset(REVIEW_DIMENSIONS),
            frozenset(STYLE_AWARE_REVIEW_DIMENSIONS),
        }:
            raise ValueError(
                "review must contain either the fixed six dimensions or "
                "the style-aware seven dimensions"
            )
        return self


class QualityScoreCapReason(BaseModel):
    """One non-compensatory rule applied after the raw weighted score."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    cap: int = Field(ge=0, le=100)
    explanation: RequiredText

    _normalize_code = field_validator("code")(_normalize_required_text)
    _normalize_explanation = field_validator("explanation")(_normalize_required_text)


class ModelReviewConflict(BaseModel):
    """An explainable disagreement without rewriting the model's raw card."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    dimension: ReviewDimensionName
    model_score: int = Field(ge=1, le=5)
    deterministic_finding_codes: list[str] = Field(min_length=1, max_length=20)
    explanation: RequiredText

    _normalize_code = field_validator("code")(_normalize_required_text)
    _normalize_finding_codes = field_validator("deterministic_finding_codes")(
        _normalize_unique_required_texts
    )
    _normalize_explanation = field_validator("explanation")(_normalize_required_text)


class ModelSelfReviewOutputError(ValueError):
    code = "model_self_review_output_invalid"


class ModelSelfReviewSchemaError(ModelSelfReviewOutputError):
    code = "model_self_review_schema_invalid"


class InvalidModelReviewEvidence(ModelSelfReviewOutputError):
    code = "invalid_model_review_evidence"


class InvalidModelReviewSourceReference(ModelSelfReviewOutputError):
    code = "invalid_model_review_source_reference"


class InvalidPersonalStyleEvidence(ModelSelfReviewOutputError):
    code = "invalid_personal_style_evidence"


class InvalidPersonalStyleClaim(ModelSelfReviewOutputError):
    code = "invalid_personal_style_claim"


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
    expected_dimensions = expected_review_dimensions(parsed_input.writing_style_context_status)
    if {dimension.dimension for dimension in parsed_output.dimensions} != set(expected_dimensions):
        raise ModelSelfReviewSchemaError(
            "review dimensions do not match writing style context readiness"
        )
    style_blocks = {
        f"writing_style_segments[{index}]": segment.text
        for index, segment in enumerate(parsed_input.writing_style_segments)
    }
    style_block_references = {
        f"writing_style_segments[{index}]": (
            segment.source_id,
            segment.source_segment_id,
        )
        for index, segment in enumerate(parsed_input.writing_style_segments)
    }
    for dimension in parsed_output.dimensions:
        narrative_fields = [
            dimension.assessment,
            dimension.limitation or "",
            *[evidence.exact_quote for evidence in dimension.evidence],
            *[evidence.exact_quote for evidence in dimension.style_sample_evidence],
        ]
        if any(_INTERNAL_SOURCE_IDENTIFIER.search(value) for value in narrative_fields):
            raise InvalidModelReviewEvidence(
                "review prose must not expose internal Source or Segment identifiers"
            )
        if parsed_input.writing_style_context_status != "ready" and any(
            _UNSUPPORTED_PERSONAL_STYLE_CLAIM.search(value) for value in narrative_fields
        ):
            raise InvalidPersonalStyleClaim(
                "review cannot claim a personal style match without ready samples"
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
        for evidence in dimension.style_sample_evidence:
            style_text = style_blocks.get(evidence.location)
            if style_text is None or evidence.exact_quote not in style_text:
                raise InvalidPersonalStyleEvidence(
                    "style evidence must be a verbatim quote from its style block"
                )
            if _reference_key(evidence.source_ref) != style_block_references[evidence.location]:
                raise InvalidPersonalStyleEvidence(
                    "style evidence source_ref must match its style block"
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

    personal_style = next(
        (
            dimension
            for dimension in parsed_output.dimensions
            if dimension.dimension == PERSONAL_STYLE_DIMENSION
        ),
        None,
    )
    if parsed_input.writing_style_context_status == "ready":
        if (
            personal_style is None
            or not personal_style.assessable
            or personal_style.score is None
            or not personal_style.evidence
            or not personal_style.style_sample_evidence
        ):
            raise InvalidPersonalStyleEvidence(
                "ready style context requires draft and style evidence"
            )
    elif personal_style is not None:
        raise InvalidPersonalStyleClaim(
            "personal style match cannot be claimed without ready style context"
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
    scoring_formula_version: Literal[
        "draft_quality_v1_60_40",
        "draft_quality_v2_non_compensatory_caps",
        "draft_quality_v3_personal_style_non_compensatory_caps",
    ] = LEGACY_DRAFT_QUALITY_FORMULA_VERSION
    writing_style_context_status: WritingStyleContextStatus = "not_provided"
    deterministic_weight: Literal[0.6] = 0.6
    model_weight: Literal[0.4] = 0.4
    experimental_model_score: float | None = Field(default=None, ge=0, le=100)
    experimental_uncapped_overall_score: float | None = Field(
        default=None,
        ge=0,
        le=100,
    )
    code_owned_score_cap: int | None = Field(default=None, ge=0, le=100)
    score_cap_reasons: list[QualityScoreCapReason] = Field(default_factory=list)
    model_review_conflicts: list[ModelReviewConflict] = Field(default_factory=list)
    experimental_overall_score: float | None = Field(default=None, ge=0, le=100)
    decision: DraftQualityDecision
    requires_human_review: Literal[True] = True

    @model_validator(mode="after")
    def report_state_and_scores_must_be_consistent(self) -> DraftQualityReport:
        """Reject persisted reports whose cards, scores, caps, or decision disagree.

        The report is read through public APIs and Markdown exporters long after
        it was first produced. Validating only each field's type would allow an
        impossible payload (for example, cap 39 with final score 80) to look
        trustworthy after a bug, partial write, or manual database edit.
        """

        if self.profile != self.deterministic.profile:
            raise ValueError("report profile must match the deterministic result")
        if (
            self.scoring_formula_version != STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION
            and self.writing_style_context_status != "not_provided"
        ):
            raise ValueError("only the v3 formula may record writing style context")

        if self.model_review_status == "completed":
            if self.model_self_review is None:
                raise ValueError("completed model review requires review cards")
            if self.model_review_unavailable_reason is not None:
                raise ValueError("completed model review cannot have an unavailable reason")
            if self.reviewer_relation is None:
                raise ValueError("completed model review requires a reviewer relation")
            expected_dimensions = expected_review_dimensions(self.writing_style_context_status)
            if {dimension.dimension for dimension in self.model_self_review.dimensions} != set(
                expected_dimensions
            ):
                raise ValueError("model review dimensions do not match writing style readiness")
        else:
            if self.model_self_review is not None:
                raise ValueError("unavailable model review cannot contain review cards")
            if self.model_review_unavailable_reason is None:
                raise ValueError("unavailable model review requires a reason")
            if self.reviewer_relation is not None:
                raise ValueError("unavailable model review cannot have a reviewer relation")

        expected_model_score: float | None = None
        if self.model_self_review is not None:
            expected_model_score = calculate_model_review_score(
                self.model_self_review,
                scoring_formula_version=self.scoring_formula_version,
                writing_style_context_status=self.writing_style_context_status,
            )
        self._require_optional_score(
            actual=self.experimental_model_score,
            expected=expected_model_score,
            field_name="experimental_model_score",
        )

        expected_uncapped = (
            None
            if expected_model_score is None
            else round(
                0.6 * self.deterministic.deterministic_score + 0.4 * expected_model_score,
                2,
            )
        )

        # Historical v1 Artifacts remain readable and immutable, but their
        # original aggregate and decision are still deterministic enough to
        # validate. Only the later cap and conflict fields are absent.
        if self.scoring_formula_version == LEGACY_DRAFT_QUALITY_FORMULA_VERSION:
            if self.experimental_uncapped_overall_score is not None:
                raise ValueError("legacy report cannot contain the v2 uncapped score field")
            if self.code_owned_score_cap is not None:
                raise ValueError("legacy report cannot contain a code-owned score cap")
            if self.score_cap_reasons:
                raise ValueError("legacy report cannot contain v2 score cap reasons")
            if self.model_review_conflicts:
                raise ValueError("legacy report cannot contain v2 model conflicts")
            self._require_optional_score(
                actual=self.experimental_overall_score,
                expected=expected_uncapped,
                field_name="experimental_overall_score",
            )
            if self.decision != self._expected_decision(
                deterministic=self.deterministic,
                model_self_review=self.model_self_review,
                expected_model_score=expected_model_score,
            ):
                raise ValueError("decision is inconsistent with review evidence and findings")
            return self

        self._require_optional_score(
            actual=self.experimental_uncapped_overall_score,
            expected=expected_uncapped,
            field_name="experimental_uncapped_overall_score",
        )

        expected_reasons: list[tuple[str, int]] = []
        metrics = self.deterministic.metrics
        duration_coverage = (
            metrics.estimated_duration_minutes / metrics.target_duration_minutes
            if metrics.target_duration_minutes
            else 0.0
        )
        if self.deterministic.has_blocker:
            expected_reasons.append(("deterministic_blocker_cap", 39))
        if duration_coverage < 0.60:
            expected_reasons.append(("duration_coverage_below_60_percent_cap", 59))
        if self.deterministic.has_warning:
            expected_reasons.append(("deterministic_warning_cap", 79))
        expected_cap = min((cap for _, cap in expected_reasons), default=100)

        if self.code_owned_score_cap != expected_cap:
            raise ValueError("code_owned_score_cap is inconsistent with deterministic findings")
        actual_reasons = [(reason.code, reason.cap) for reason in self.score_cap_reasons]
        if actual_reasons != expected_reasons:
            raise ValueError("score_cap_reasons are inconsistent with deterministic findings")

        expected_conflicts: list[tuple[str, ReviewDimensionName, int, tuple[str, ...]]] = []
        duration_finding = next(
            (
                finding
                for finding in self.deterministic.findings
                if finding.code == "draft.empty" or finding.code.startswith("duration.")
            ),
            None,
        )
        maximum_brief_score: int | None = None
        if duration_finding is not None:
            if duration_finding.status == "blocker" or duration_coverage < 0.60:
                maximum_brief_score = 2
            elif duration_finding.status == "warning":
                maximum_brief_score = 3
        if self.model_self_review is not None and maximum_brief_score is not None:
            brief_card = next(
                (
                    dimension
                    for dimension in self.model_self_review.dimensions
                    if dimension.dimension == "brief_adherence"
                ),
                None,
            )
            if (
                brief_card is not None
                and brief_card.assessable
                and brief_card.score is not None
                and brief_card.score > maximum_brief_score
                and duration_finding is not None
            ):
                expected_conflicts.append(
                    (
                        "duration_vs_brief_adherence_score",
                        "brief_adherence",
                        brief_card.score,
                        (duration_finding.code,),
                    )
                )
        actual_conflicts = [
            (
                conflict.code,
                conflict.dimension,
                conflict.model_score,
                tuple(conflict.deterministic_finding_codes),
            )
            for conflict in self.model_review_conflicts
        ]
        if actual_conflicts != expected_conflicts:
            raise ValueError(
                "model_review_conflicts are inconsistent with model cards and findings"
            )

        expected_final = (
            None if expected_uncapped is None else min(expected_uncapped, float(expected_cap))
        )
        self._require_optional_score(
            actual=self.experimental_overall_score,
            expected=expected_final,
            field_name="experimental_overall_score",
        )

        expected_decision = self._expected_decision(
            deterministic=self.deterministic,
            model_self_review=self.model_self_review,
            expected_model_score=expected_model_score,
        )
        if self.decision != expected_decision:
            raise ValueError("decision is inconsistent with review evidence and findings")
        return self

    @staticmethod
    def _expected_decision(
        *,
        deterministic: DeterministicDraftQualityResult,
        model_self_review: ModelSelfReviewOutput | None,
        expected_model_score: float | None,
    ) -> DraftQualityDecision:
        if deterministic.has_blocker:
            return "blocked"
        if model_self_review is None or expected_model_score is None:
            return "automated_review_incomplete"
        low_model_dimension = any(
            dimension.assessable and dimension.score is not None and dimension.score <= 2
            for dimension in model_self_review.dimensions
        )
        return (
            "revision_recommended"
            if deterministic.has_warning or low_model_dimension
            else "candidate_ready_for_human_review"
        )

    @staticmethod
    def _require_optional_score(
        *,
        actual: float | None,
        expected: float | None,
        field_name: str,
    ) -> None:
        if actual is None or expected is None:
            if actual is not expected:
                raise ValueError(f"{field_name} is inconsistent with its inputs")
            return
        if abs(actual - expected) > 0.005:
            raise ValueError(f"{field_name} is inconsistent with its inputs")


class DraftQualityReportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: DraftQualityReport
    artifact: ArtifactView
