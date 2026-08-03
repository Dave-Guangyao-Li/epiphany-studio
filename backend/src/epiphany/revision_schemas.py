from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from epiphany.draft_feedback_schemas import FeedbackDecision, FeedbackOrigin
from epiphany.draft_quality_schemas import DraftQualityReport
from epiphany.editor_schemas import (
    GroundedDraftParagraph,
    InvalidPodcastDraftSourceReference,
    MissingInitialSourceReference,
    MissingSupplementalSourceReference,
    PodcastDraftOutput,
    PodcastDraftSchemaError,
    PodcastDraftSection,
    PodcastDraftTaskInput,
    PodcastDraftTitleTopicMismatch,
    WritingStyleSampleLeak,
    editor_spoken_script_reference_keys,
    validate_podcast_draft_output,
)
from epiphany.quality_contract_schemas import DURATION_TOLERANCE_RATIO
from epiphany.schemas import ArtifactView, RunView, SourceReference

LEGACY_DRAFT_IMPROVEMENT_PLAN_VERSION = "draft_improvement_plan_v1"
DRAFT_IMPROVEMENT_PLAN_VERSION = "draft_improvement_plan_v2_recovery_history"
LEGACY_DRAFT_REVISION_REQUEST_VERSION = "draft_revision_request_v1"
DRAFT_REVISION_REQUEST_VERSION = "draft_revision_request_v2_supplemental_interview"
DRAFT_REVISION_COMPARISON_VERSION = "draft_revision_comparison_v1"
REVISE_PODCAST_DRAFT = "revise_podcast_draft"
MAX_LENGTH_RECOVERY_PRIORITY_REFS = 12
PODCAST_REVISION_PATCH_VERSION = "podcast_revision_patch_v1"

DurationResolution = Literal[
    "not_needed",
    "reuse_unused_material",
    "reuse_then_supplement",
    "add_supplemental_material",
]
ImprovementGapKind = Literal[
    "duration_shortfall",
    "scaffold_material_gap",
    "deterministic_quality",
    "model_review",
    "selected_feedback",
]
ImprovementGapSeverity = Literal["info", "warning", "blocker"]
ImprovementOptionKind = Literal[
    "reuse_unused_material",
    "add_supplemental_material",
    "lower_target_duration",
    "apply_selected_feedback",
]
QuestionAnchorKind = Literal["material_gap", "scaffold_question"]
RevisionAction = Literal[
    "reuse_unused_material",
    "add_supplemental_material",
    "lower_target_duration",
    "apply_selected_feedback",
]
LengthRecoveryReadiness = Literal[
    "not_needed",
    # Wire value retained for persisted v1 compatibility. It means only that
    # the selected candidate segments contain enough raw characters to justify
    # one bounded Revision attempt; it does not promise that a grounded,
    # non-repetitive draft can reach the duration bound.
    "existing_material_sufficient",
    "existing_material_partial",
    "additional_material_required",
]


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


def _non_whitespace_character_count(value: str) -> int:
    return len("".join(value.split()))


def _spoken_script_character_count(draft: PodcastDraftOutput) -> int:
    texts = [
        draft.podcast_script.opening.text,
        *[
            paragraph.text
            for section in draft.podcast_script.sections
            for paragraph in section.paragraphs
        ],
        draft.podcast_script.closing.text,
    ]
    return sum(_non_whitespace_character_count(text) for text in texts)


def duration_character_bounds(target_character_count: int) -> tuple[int, int]:
    """Return the code-owned spoken-character acceptance interval."""

    tolerance = Decimal(str(DURATION_TOLERANCE_RATIO))
    target = Decimal(target_character_count)
    return (
        math.ceil(target * (Decimal(1) - tolerance)),
        math.floor(target * (Decimal(1) + tolerance)),
    )


class DraftDurationGap(BaseModel):
    """Exact character accounting for the spoken script only."""

    model_config = ConfigDict(extra="forbid")

    target_duration_minutes: int = Field(gt=0)
    speaking_rate_chars_per_minute: int = Field(gt=0)
    target_script_character_count: int = Field(ge=0)
    actual_script_character_count: int = Field(ge=0)
    estimated_duration_minutes: float = Field(ge=0)
    duration_coverage_ratio: float = Field(ge=0)
    missing_script_character_count: int = Field(ge=0)
    missing_duration_minutes: float = Field(ge=0)

    @model_validator(mode="after")
    def values_must_describe_one_exact_duration_gap(self) -> DraftDurationGap:
        expected_target = self.target_duration_minutes * self.speaking_rate_chars_per_minute
        if self.target_script_character_count != expected_target:
            raise ValueError("target character count is inconsistent with duration")
        expected_missing = max(
            0,
            self.target_script_character_count - self.actual_script_character_count,
        )
        if self.missing_script_character_count != expected_missing:
            raise ValueError("missing character count is inconsistent with the script")
        expected_minutes = round(
            self.actual_script_character_count / self.speaking_rate_chars_per_minute,
            2,
        )
        if self.estimated_duration_minutes != expected_minutes:
            raise ValueError("estimated duration is inconsistent with the script")
        expected_coverage = round(
            self.estimated_duration_minutes / self.target_duration_minutes,
            4,
        )
        if self.duration_coverage_ratio != expected_coverage:
            raise ValueError("duration coverage is inconsistent with the script")
        expected_missing_minutes = round(
            self.missing_script_character_count / self.speaking_rate_chars_per_minute,
            2,
        )
        if self.missing_duration_minutes != expected_missing_minutes:
            raise ValueError("missing duration is inconsistent with missing characters")
        return self


class UnusedFactualMaterial(BaseModel):
    """Source inventory without copying any SourceSegment text into the plan.

    ``unused_*`` is the complete uncited inventory. The separately assessed
    ``priority_candidate_*`` fields are the small, deterministic shortlist that
    may justify one length-recovery attempt. Raw unused volume is therefore not
    presented as a promise that all of it belongs in the spoken draft.

    ``priority_candidates_assessed=False`` keeps already-persisted v1 plans
    readable. Plans built by current code always assess and persist the
    shortlist, including an explicitly empty shortlist.
    """

    model_config = ConfigDict(extra="forbid")

    total_factual_segment_count: int = Field(ge=0)
    cited_factual_segment_count: int = Field(ge=0)
    unused_factual_segment_count: int = Field(ge=0)
    unused_factual_character_count: int = Field(ge=0)
    unused_source_refs: list[SourceReference] = Field(default_factory=list, max_length=1_000)
    priority_candidates_assessed: bool = False
    priority_candidate_character_count: int = Field(default=0, ge=0)
    priority_candidate_source_refs: list[SourceReference] = Field(
        default_factory=list,
        max_length=MAX_LENGTH_RECOVERY_PRIORITY_REFS,
    )

    _source_refs_are_unique = field_validator(
        "unused_source_refs",
        "priority_candidate_source_refs",
    )(_unique_references)

    @model_validator(mode="after")
    def counts_must_match_references(self) -> UnusedFactualMaterial:
        if (
            self.cited_factual_segment_count + self.unused_factual_segment_count
            != self.total_factual_segment_count
        ):
            raise ValueError("factual segment counts must add up")
        if self.unused_factual_segment_count != len(self.unused_source_refs):
            raise ValueError("unused count must match unused_source_refs")
        if not self.priority_candidates_assessed:
            if self.priority_candidate_character_count or self.priority_candidate_source_refs:
                raise ValueError("unassessed material cannot contain priority candidate evidence")
            return self

        unused_keys = {_reference_key(reference) for reference in self.unused_source_refs}
        priority_keys = {
            _reference_key(reference) for reference in self.priority_candidate_source_refs
        }
        if not priority_keys <= unused_keys:
            raise ValueError("priority candidates must be a subset of unused references")
        if bool(priority_keys) != bool(self.priority_candidate_character_count):
            raise ValueError("priority candidate references must match candidate characters")
        if self.priority_candidate_character_count > self.unused_factual_character_count:
            raise ValueError("priority candidate characters cannot exceed unused characters")
        return self


class DraftImprovementGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=200)
    kind: ImprovementGapKind
    severity: ImprovementGapSeverity
    explanation: str = Field(min_length=1, max_length=2_000)
    source_refs: list[SourceReference] = Field(default_factory=list, max_length=20)

    _normalize_text = field_validator("code", "explanation")(_normalize_required_text)
    _source_refs_are_unique = field_validator("source_refs")(_unique_references)


class DraftImprovementOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ImprovementOptionKind
    recommended: bool
    explanation: str = Field(min_length=1, max_length=2_000)
    source_refs: list[SourceReference] = Field(default_factory=list, max_length=1_000)
    suggested_target_duration_minutes: int | None = Field(default=None, gt=0)

    _normalize_explanation = field_validator("explanation")(_normalize_required_text)
    _source_refs_are_unique = field_validator("source_refs")(_unique_references)

    @model_validator(mode="after")
    def target_is_only_valid_for_duration_option(self) -> DraftImprovementOption:
        if self.kind == "lower_target_duration":
            if self.suggested_target_duration_minutes is None:
                raise ValueError("lower_target_duration requires a suggested target")
        elif self.suggested_target_duration_minutes is not None:
            raise ValueError("only lower_target_duration may suggest a target")
        return self


class TargetedSupplementQuestion(BaseModel):
    """One question traceably derived from an existing Interview Scaffold."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=2_000)
    purpose: str = Field(min_length=1, max_length=2_000)
    anchor_kind: QuestionAnchorKind
    anchor_path: str = Field(min_length=1, max_length=500)
    anchor_text: str = Field(min_length=1, max_length=1_000)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=10)

    _normalize_text = field_validator(
        "prompt",
        "purpose",
        "anchor_path",
        "anchor_text",
    )(_normalize_required_text)
    _normalize_keywords = field_validator("keywords")(_normalize_unique_required_texts)
    _source_refs_are_unique = field_validator("source_refs")(_unique_references)


class DraftImprovementPlan(BaseModel):
    """Deterministic, versioned bridge from quality evidence to a user choice."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "draft_improvement_plan_v1",
        "draft_improvement_plan_v2_recovery_history",
    ] = DRAFT_IMPROVEMENT_PLAN_VERSION
    parent_run_id: str = Field(min_length=1, max_length=200)
    parent_draft_artifact_id: str = Field(min_length=1, max_length=200)
    quality_report_artifact_id: str = Field(min_length=1, max_length=200)
    writing_style_context_available: bool
    prior_length_recovery_attempted: bool = False
    selected_feedback_codes: list[str] = Field(default_factory=list, max_length=50)
    duration: DraftDurationGap
    material: UnusedFactualMaterial
    duration_resolution: DurationResolution
    gaps: list[DraftImprovementGap] = Field(default_factory=list, max_length=100)
    options: list[DraftImprovementOption] = Field(default_factory=list, max_length=4)
    targeted_questions: list[TargetedSupplementQuestion] = Field(
        min_length=3,
        max_length=6,
    )

    _normalize_ids = field_validator(
        "parent_run_id",
        "parent_draft_artifact_id",
        "quality_report_artifact_id",
    )(_normalize_required_text)
    _normalize_feedback_codes = field_validator("selected_feedback_codes")(
        _normalize_unique_required_texts
    )

    @model_validator(mode="after")
    def option_and_resolution_state_must_be_consistent(self) -> DraftImprovementPlan:
        if (
            self.schema_version == LEGACY_DRAFT_IMPROVEMENT_PLAN_VERSION
            and self.prior_length_recovery_attempted
        ):
            raise ValueError("legacy improvement plans cannot record recovery history")
        option_kinds = [option.kind for option in self.options]
        if len(option_kinds) != len(set(option_kinds)):
            raise ValueError("improvement option kinds must be unique")
        question_keys = [
            (question.anchor_path, question.prompt) for question in self.targeted_questions
        ]
        if len(question_keys) != len(set(question_keys)):
            raise ValueError("targeted questions must be unique")
        if ("apply_selected_feedback" in option_kinds) != bool(self.selected_feedback_codes):
            raise ValueError("apply_selected_feedback must match selected_feedback_codes")
        minimum_characters, _maximum_characters = duration_character_bounds(
            self.duration.target_script_character_count
        )
        below_minimum = self.duration.actual_script_character_count < minimum_characters
        if self.duration_resolution == "not_needed":
            if below_minimum:
                raise ValueError(
                    "not_needed requires the spoken script to reach the duration lower bound"
                )
        elif (
            not below_minimum
            and self.duration.actual_script_character_count
            >= self.duration.target_script_character_count
        ):
            raise ValueError("a duration resolution requires a spoken-script shortfall")
        # Compatibility: pre-M3.8 v1 plans used the center target, not the 85%
        # lower bound, when deciding whether a duration resolution was needed.
        # Such persisted plans remain readable, while the current builder below
        # always emits ``not_needed`` once the lower bound has been reached.
        return self


class DraftImprovementPlanRecord(BaseModel):
    """Public JSON view of one persisted deterministic improvement plan."""

    model_config = ConfigDict(extra="forbid")

    plan: DraftImprovementPlan
    artifact: ArtifactView


class CreateDraftRevisionRequest(BaseModel):
    """One explicit, idempotent human decision to create a child Revision Run."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[
        "draft_revision_request_v1",
        "draft_revision_request_v2_supplemental_interview",
    ] = DRAFT_REVISION_REQUEST_VERSION
    submission_id: str = Field(min_length=1, max_length=200)
    selected_actions: list[RevisionAction] = Field(min_length=1, max_length=4)
    selected_feedback_artifact_ids: list[str] = Field(default_factory=list, max_length=20)
    selected_gap_codes: list[str] = Field(default_factory=list, max_length=50)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    supplemental_interview_plan_artifact_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    answered_question_ids: list[str] = Field(default_factory=list, max_length=6)
    target_duration_minutes: Literal[10, 15, 30] | None = None
    revision_instruction: str | None = Field(default=None, min_length=1, max_length=2_000)

    _normalize_submission_id = field_validator("submission_id")(_normalize_required_text)
    _normalize_gap_codes = field_validator("selected_gap_codes")(_normalize_unique_required_texts)

    @field_validator(
        "selected_actions",
        "selected_feedback_artifact_ids",
        "source_ids",
        "answered_question_ids",
    )
    @classmethod
    def lists_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("items must be unique")
        return value

    @field_validator("revision_instruction")
    @classmethod
    def normalize_optional_instruction(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_required_text(value)

    @field_validator("supplemental_interview_plan_artifact_id")
    @classmethod
    def normalize_optional_plan_id(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_required_text(value)

    @model_validator(mode="after")
    def actions_must_match_supplied_choices(self) -> CreateDraftRevisionRequest:
        actions = set(self.selected_actions)
        if ("apply_selected_feedback" in actions) != bool(
            self.selected_feedback_artifact_ids or self.selected_gap_codes
        ):
            raise ValueError(
                "apply_selected_feedback must match selected feedback artifacts or gap codes"
            )
        if ("add_supplemental_material" in actions) != bool(self.source_ids):
            raise ValueError("add_supplemental_material must match source_ids")
        interview_provenance_present = bool(
            self.supplemental_interview_plan_artifact_id or self.answered_question_ids
        )
        if interview_provenance_present and (
            "add_supplemental_material" not in actions
            or self.supplemental_interview_plan_artifact_id is None
            or not self.answered_question_ids
        ):
            raise ValueError(
                "supplemental interview provenance requires add_supplemental_material, "
                "one plan artifact, and answered question IDs"
            )
        if self.version == LEGACY_DRAFT_REVISION_REQUEST_VERSION and interview_provenance_present:
            raise ValueError("legacy revision requests cannot carry supplemental interview data")
        if ("lower_target_duration" in actions) != (self.target_duration_minutes is not None):
            raise ValueError("lower_target_duration must match target_duration_minutes")
        if {"reuse_unused_material", "lower_target_duration"} <= actions:
            raise ValueError(
                "reuse_unused_material and lower_target_duration are alternative actions"
            )
        return self


class DraftRevisionRequestRecord(BaseModel):
    """Persisted request provenance; the child Run is never created implicitly."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[
        "draft_revision_request_v1",
        "draft_revision_request_v2_supplemental_interview",
    ] = DRAFT_REVISION_REQUEST_VERSION
    submission_id: str
    parent_run_id: str
    child_run_id: str
    plan_artifact_id: str
    parent_draft_artifact_id: str
    parent_quality_report_artifact_id: str
    selected_actions: list[RevisionAction]
    selected_feedback_artifact_ids: list[str]
    selected_gap_codes: list[str]
    source_ids: list[str]
    supplemental_interview_plan_artifact_id: str | None = None
    answered_question_ids: list[str] = Field(default_factory=list, max_length=6)
    target_duration_minutes: Literal[10, 15, 30] | None
    revision_instruction: str | None

    _normalize_ids = field_validator(
        "submission_id",
        "parent_run_id",
        "child_run_id",
        "plan_artifact_id",
        "parent_draft_artifact_id",
        "parent_quality_report_artifact_id",
    )(_normalize_required_text)

    @field_validator(
        "selected_actions",
        "selected_feedback_artifact_ids",
        "selected_gap_codes",
        "source_ids",
        "answered_question_ids",
    )
    @classmethod
    def record_lists_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("items must be unique")
        return value

    @field_validator("supplemental_interview_plan_artifact_id")
    @classmethod
    def normalize_record_plan_id(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_required_text(value)

    @model_validator(mode="after")
    def supplemental_provenance_must_be_complete(self) -> DraftRevisionRequestRecord:
        provenance_present = bool(
            self.supplemental_interview_plan_artifact_id or self.answered_question_ids
        )
        if provenance_present and (
            "add_supplemental_material" not in self.selected_actions
            or self.supplemental_interview_plan_artifact_id is None
            or not self.answered_question_ids
        ):
            raise ValueError("persisted supplemental interview provenance is incomplete")
        if self.version == LEGACY_DRAFT_REVISION_REQUEST_VERSION and provenance_present:
            raise ValueError("legacy revision records cannot carry supplemental interview data")
        return self


class CreateDraftRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotent_replay: bool
    request_artifact_id: str
    improvement_plan: DraftImprovementPlanRecord
    run: RunView


class SelectedRevisionFeedback(BaseModel):
    """One append-only feedback record explicitly selected by the user."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=200)
    feedback_origin: FeedbackOrigin
    decision: FeedbackDecision
    overall_rating: int = Field(ge=1, le=5)
    voice_match_rating: int = Field(ge=1, le=5)
    recordability_rating: int = Field(ge=1, le=5)
    usefulness_rating: int = Field(ge=1, le=5)
    tone_fit_rating: int = Field(ge=1, le=5)
    would_record_as_is: bool
    observed_duration_minutes: float | None = Field(default=None, gt=0, le=180)
    comment: str | None = Field(default=None, min_length=1, max_length=2_000)

    _normalize_artifact_id = field_validator("artifact_id")(_normalize_required_text)

    @field_validator("comment")
    @classmethod
    def normalize_optional_comment(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_required_text(value)


class DraftLengthRecoveryPlan(BaseModel):
    """Deterministic evidence plan for one bounded length-recovery attempt.

    Counts describe only the words in ``podcast_script`` that will be spoken.
    Source references identify existing factual segments to consider; they are
    candidates, not a requirement to force every Source into the new draft.
    ``existing_material_sufficient`` is a compatibility wire value meaning
    *candidate raw volume is sufficient to try once*. It is not a prediction
    that the Revision will reach the bound after relevance, compression,
    repetition, and quality checks.
    """

    model_config = ConfigDict(extra="forbid")

    actual_script_character_count: int = Field(ge=0)
    minimum_script_character_count: int = Field(ge=1)
    target_script_character_count: int = Field(ge=1)
    maximum_script_character_count: int = Field(ge=1)
    missing_to_minimum_character_count: int = Field(ge=0)
    missing_to_target_character_count: int = Field(ge=0)
    available_unused_character_count: int = Field(ge=0)
    readiness: LengthRecoveryReadiness
    priority_unused_source_refs: list[SourceReference] = Field(
        default_factory=list,
        max_length=1_000,
    )

    _priority_source_refs_are_unique = field_validator("priority_unused_source_refs")(
        _unique_references
    )

    @model_validator(mode="after")
    def counts_and_readiness_must_be_consistent(self) -> DraftLengthRecoveryPlan:
        expected_minimum, expected_maximum = duration_character_bounds(
            self.target_script_character_count
        )
        if self.minimum_script_character_count != expected_minimum:
            raise ValueError("minimum character count must use the configured duration tolerance")
        if self.maximum_script_character_count != expected_maximum:
            raise ValueError("maximum character count must use the configured duration tolerance")
        if self.missing_to_minimum_character_count != max(
            0,
            self.minimum_script_character_count - self.actual_script_character_count,
        ):
            raise ValueError("missing-to-minimum count is inconsistent with the script")
        if self.missing_to_target_character_count != max(
            0,
            self.target_script_character_count - self.actual_script_character_count,
        ):
            raise ValueError("missing-to-target count is inconsistent with the script")

        if not self.missing_to_minimum_character_count:
            expected_readiness: LengthRecoveryReadiness = "not_needed"
        elif self.available_unused_character_count >= self.missing_to_minimum_character_count:
            expected_readiness = "existing_material_sufficient"
        elif self.available_unused_character_count:
            expected_readiness = "existing_material_partial"
        else:
            expected_readiness = "additional_material_required"
        if self.readiness != expected_readiness:
            raise ValueError(
                "length-recovery readiness is inconsistent with candidate material volume"
            )
        if bool(self.priority_unused_source_refs) != bool(self.available_unused_character_count):
            raise ValueError("priority unused references must match available unused characters")
        return self


def build_draft_length_recovery_plan(
    *,
    improvement_plan: DraftImprovementPlan,
    target_duration_minutes: int,
) -> DraftLengthRecoveryPlan:
    """Project an Improvement Plan into exact instructions for a child Revision."""

    rate = improvement_plan.duration.speaking_rate_chars_per_minute
    actual = improvement_plan.duration.actual_script_character_count
    target = target_duration_minutes * rate
    minimum, maximum = duration_character_bounds(target)
    missing_to_minimum = max(0, minimum - actual)
    material = improvement_plan.material
    if material.priority_candidates_assessed:
        priority_refs = material.priority_candidate_source_refs
        available = material.priority_candidate_character_count
    else:
        # Compatibility for Improvement Plans persisted before candidate
        # shortlisting existed. New plans never use this branch.
        priority_refs = material.unused_source_refs
        available = material.unused_factual_character_count
    if not missing_to_minimum:
        readiness: LengthRecoveryReadiness = "not_needed"
    elif available >= missing_to_minimum:
        readiness = "existing_material_sufficient"
    elif available:
        readiness = "existing_material_partial"
    else:
        readiness = "additional_material_required"
    return DraftLengthRecoveryPlan(
        actual_script_character_count=actual,
        minimum_script_character_count=minimum,
        target_script_character_count=target,
        maximum_script_character_count=maximum,
        missing_to_minimum_character_count=missing_to_minimum,
        missing_to_target_character_count=max(0, target - actual),
        available_unused_character_count=available,
        readiness=readiness,
        priority_unused_source_refs=priority_refs,
    )


class PodcastRevisionTaskInput(PodcastDraftTaskInput):
    """Strict Editor input for one explicit child Revision Run."""

    task_kind: Literal["revise_podcast_draft"]
    parent_run_id: str = Field(min_length=1, max_length=200)
    parent_draft_artifact_id: str = Field(min_length=1, max_length=200)
    parent_quality_report_artifact_id: str = Field(min_length=1, max_length=200)
    plan_artifact_id: str = Field(min_length=1, max_length=200)
    request_artifact_id: str = Field(min_length=1, max_length=200)
    supplemental_interview_round: Literal[0, 1, 2] = 0
    parent_podcast_draft: PodcastDraftOutput
    selected_actions: list[RevisionAction] = Field(min_length=1, max_length=4)
    selected_feedback: list[SelectedRevisionFeedback] = Field(
        default_factory=list,
        max_length=20,
    )
    selected_quality_gaps: list[DraftImprovementGap] = Field(
        default_factory=list,
        max_length=50,
    )
    added_source_ids: list[str] = Field(default_factory=list, max_length=20)
    length_recovery_plan: DraftLengthRecoveryPlan | None = None
    revision_instruction: str | None = Field(default=None, min_length=1, max_length=2_000)

    _normalize_revision_ids = field_validator(
        "parent_run_id",
        "parent_draft_artifact_id",
        "parent_quality_report_artifact_id",
        "plan_artifact_id",
        "request_artifact_id",
    )(_normalize_required_text)

    @field_validator("selected_actions")
    @classmethod
    def selected_actions_must_be_unique(
        cls,
        value: list[RevisionAction],
    ) -> list[RevisionAction]:
        if len(value) != len(set(value)):
            raise ValueError("selected_actions must be unique")
        return value

    @field_validator("added_source_ids")
    @classmethod
    def added_source_ids_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("added_source_ids must be unique")
        return value

    @field_validator("revision_instruction")
    @classmethod
    def normalize_task_instruction(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_required_text(value)

    @model_validator(mode="after")
    def selected_inputs_must_match_actions(self) -> PodcastRevisionTaskInput:
        actions = set(self.selected_actions)
        if ("apply_selected_feedback" in actions) != bool(
            self.selected_feedback or self.selected_quality_gaps
        ):
            raise ValueError("apply_selected_feedback must match selected feedback or quality gaps")
        if len({feedback.artifact_id for feedback in self.selected_feedback}) != len(
            self.selected_feedback
        ):
            raise ValueError("selected feedback artifacts must be unique")
        if len({gap.code for gap in self.selected_quality_gaps}) != len(self.selected_quality_gaps):
            raise ValueError("selected quality gaps must be unique")
        if self.added_source_ids and "add_supplemental_material" not in actions:
            raise ValueError("added_source_ids require the add_supplemental_material action")
        if self.length_recovery_plan is not None and "reuse_unused_material" not in actions:
            raise ValueError("a length_recovery_plan requires reuse_unused_material")
        # Compatibility: already-persisted v8 Revision Tasks created before
        # M3.8 may select reuse_unused_material without the new deterministic
        # length_recovery_plan. Internal creation now always adds the plan, but
        # the parser keeps those queued Tasks resumable.

        if self.length_recovery_plan is not None:
            recovery = self.length_recovery_plan
            if self.creative_brief is None:
                raise ValueError("length recovery requires a Creative Brief")
            expected_target = (
                self.creative_brief.target_duration_minutes
                * self.creative_brief.speaking_rate_chars_per_minute
            )
            if recovery.target_script_character_count != expected_target:
                raise ValueError("length-recovery target must match the Creative Brief")
            if recovery.actual_script_character_count != _spoken_script_character_count(
                self.parent_podcast_draft
            ):
                raise ValueError("length-recovery actual count must match the parent Draft")

            factual_segments = [
                *self.initial_source_segments,
                *self.supplemental_source_segments,
            ]
            factual_by_key = {
                (segment.source_id, segment.source_segment_id): segment
                for segment in factual_segments
            }
            priority_keys = {
                _reference_key(reference) for reference in recovery.priority_unused_source_refs
            }
            if not priority_keys <= set(factual_by_key):
                raise ValueError(
                    "length-recovery references must resolve to factual source segments"
                )
            spoken_keys = set(
                editor_spoken_script_reference_keys(
                    self.parent_podcast_draft.model_dump(mode="json")
                )
            )
            if priority_keys & spoken_keys:
                raise ValueError(
                    "length-recovery priority references must be unused by the spoken parent Draft"
                )
            available_characters = sum(
                _non_whitespace_character_count(factual_by_key[key].text) for key in priority_keys
            )
            if available_characters != recovery.available_unused_character_count:
                raise ValueError(
                    "available unused characters must match the priority source segments"
                )

        validate_podcast_draft_output(
            task_input=revision_base_editor_input(self.model_dump(mode="json")),
            content=self.parent_podcast_draft.model_dump(mode="json"),
        )
        return self


class PodcastRevisionOutputError(ValueError):
    code = "podcast_revision_output_invalid"


class PodcastRevisionTaskInputError(ValueError):
    """Trusted Revision input was invalid before model output could be checked."""

    code = "podcast_revision_task_input_invalid"


class PodcastRevisionSchemaError(PodcastRevisionOutputError):
    code = "podcast_revision_schema_invalid"


class PodcastRevisionPatchSchemaError(PodcastRevisionOutputError):
    code = "podcast_revision_patch_schema_invalid"


class InvalidPodcastRevisionSourceReference(PodcastRevisionOutputError):
    code = "podcast_revision_invalid_source_reference"


class PodcastRevisionTitleTopicMismatch(PodcastRevisionOutputError):
    code = "podcast_revision_title_topic_mismatch"


class PodcastRevisionMissingInitialSourceReference(PodcastRevisionOutputError):
    code = "podcast_revision_missing_initial_source_reference"


class PodcastRevisionMissingSupplementalSourceReference(PodcastRevisionOutputError):
    code = "podcast_revision_missing_supplemental_source_reference"


class PodcastRevisionWritingStyleSampleLeak(PodcastRevisionOutputError):
    code = "podcast_revision_writing_style_sample_leak"


class PodcastRevisionNoChange(PodcastRevisionOutputError):
    code = "podcast_revision_no_change"


class PodcastRevisionAddedMaterialUnused(PodcastRevisionOutputError):
    code = "podcast_revision_added_material_unused"


class PodcastRevisionRecoveryMaterialUnused(PodcastRevisionOutputError):
    code = "podcast_revision_recovery_material_unused"


class PodcastRevisionSectionAppend(BaseModel):
    """Bounded paragraphs to append to one immutable parent section."""

    model_config = ConfigDict(extra="forbid")

    section_index: int = Field(ge=0, le=7)
    paragraphs: list[GroundedDraftParagraph] = Field(min_length=1, max_length=4)


class PodcastRevisionPatch(BaseModel):
    """Small hosted-model output that is deterministically applied to a parent Draft."""

    model_config = ConfigDict(extra="forbid")

    patch_version: Literal["podcast_revision_patch_v1"] = PODCAST_REVISION_PATCH_VERSION
    append_to_sections: list[PodcastRevisionSectionAppend] = Field(
        default_factory=list,
        max_length=8,
    )
    new_sections: list[PodcastDraftSection] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def patch_must_contain_one_unique_operation(self) -> PodcastRevisionPatch:
        if not self.append_to_sections and not self.new_sections:
            raise ValueError("revision patch must contain at least one operation")
        section_indexes = [operation.section_index for operation in self.append_to_sections]
        if len(section_indexes) != len(set(section_indexes)):
            raise ValueError("revision patch section indexes must be unique")
        return self


def _materialize_podcast_revision_patch(
    *,
    parsed_input: PodcastRevisionTaskInput,
    content: dict[str, Any],
) -> dict[str, Any]:
    """Apply a bounded patch locally while preserving the immutable parent structure."""

    try:
        patch = PodcastRevisionPatch.model_validate(content)
    except (ValidationError, ValueError, TypeError) as error:
        raise PodcastRevisionPatchSchemaError(
            "podcast revision patch did not match its bounded schema"
        ) from error

    candidate = parsed_input.parent_podcast_draft.model_copy(deep=True)
    for operation in patch.append_to_sections:
        if operation.section_index >= len(candidate.podcast_script.sections):
            raise PodcastRevisionPatchSchemaError(
                "podcast revision patch referenced an unavailable parent section"
            )
        section = candidate.podcast_script.sections[operation.section_index]
        section.paragraphs.extend(operation.paragraphs)
    candidate.podcast_script.sections.extend(patch.new_sections)

    try:
        return PodcastDraftOutput.model_validate(candidate.model_dump(mode="json")).model_dump(
            mode="json"
        )
    except (ValidationError, ValueError, TypeError) as error:
        raise PodcastRevisionPatchSchemaError(
            "podcast revision patch exceeded the bounded parent Draft structure"
        ) from error


def revision_base_editor_input(task_input: dict[str, Any]) -> dict[str, Any]:
    """Project the Revision contract onto the common strict Editor input."""

    projected = {
        field_name: task_input[field_name]
        for field_name in PodcastDraftTaskInput.model_fields
        if field_name != "task_kind" and field_name in task_input
    }
    projected["task_kind"] = "build_podcast_draft"
    return projected


def validate_podcast_revision_output(
    *,
    task_input: dict[str, Any],
    content: dict[str, Any],
) -> dict[str, Any]:
    try:
        parsed_input = PodcastRevisionTaskInput.model_validate(task_input)
    except (ValidationError, ValueError, TypeError) as error:
        raise PodcastRevisionTaskInputError(
            "podcast revision task input did not match its trusted contract"
        ) from error

    expects_length_recovery_patch = (
        "reuse_unused_material" in parsed_input.selected_actions
        and parsed_input.length_recovery_plan is not None
    )
    candidate_content = content
    if expects_length_recovery_patch and "podcast_script" not in content:
        candidate_content = _materialize_podcast_revision_patch(
            parsed_input=parsed_input,
            content=content,
        )
    elif "patch_version" in content:
        raise PodcastRevisionPatchSchemaError(
            "podcast revision patches are only valid for planned length recovery"
        )

    try:
        validated = validate_podcast_draft_output(
            task_input=revision_base_editor_input(task_input),
            content=candidate_content,
        )
    except PodcastDraftSchemaError as error:
        raise PodcastRevisionSchemaError(
            "podcast revision did not match the strict PodcastDraft schema"
        ) from error
    except InvalidPodcastDraftSourceReference as error:
        raise InvalidPodcastRevisionSourceReference(
            "podcast revision cited a source outside the Revision task scope"
        ) from error
    except PodcastDraftTitleTopicMismatch as error:
        raise PodcastRevisionTitleTopicMismatch(
            "podcast revision title did not exactly match the requested topic"
        ) from error
    except MissingInitialSourceReference as error:
        raise PodcastRevisionMissingInitialSourceReference(
            "podcast revision omitted every initial factual source reference"
        ) from error
    except MissingSupplementalSourceReference as error:
        raise PodcastRevisionMissingSupplementalSourceReference(
            "podcast revision omitted required supplemental source evidence"
        ) from error
    except WritingStyleSampleLeak as error:
        raise PodcastRevisionWritingStyleSampleLeak(
            "podcast revision copied distinctive text from a style-only sample"
        ) from error
    if validated == parsed_input.parent_podcast_draft.model_dump(mode="json"):
        raise PodcastRevisionNoChange("podcast revision must produce a changed Draft candidate")

    parent_spoken_texts = {
        _normalize_required_text(parsed_input.parent_podcast_draft.podcast_script.opening.text),
        *(
            _normalize_required_text(paragraph.text)
            for section in parsed_input.parent_podcast_draft.podcast_script.sections
            for paragraph in section.paragraphs
        ),
        _normalize_required_text(parsed_input.parent_podcast_draft.podcast_script.closing.text),
    }
    revised = PodcastDraftOutput.model_validate(validated)
    revised_spoken_units = [
        revised.podcast_script.opening,
        *[
            paragraph
            for section in revised.podcast_script.sections
            for paragraph in section.paragraphs
        ],
        revised.podcast_script.closing,
    ]

    if (
        "add_supplemental_material" in parsed_input.selected_actions
        and parsed_input.added_source_ids
    ):
        added_source_ids = set(parsed_input.added_source_ids)
        added_material_reached_new_spoken_text = any(
            any(reference.source_id in added_source_ids for reference in unit.source_refs)
            and _normalize_required_text(unit.text) not in parent_spoken_texts
            for unit in revised_spoken_units
        )
        if not added_material_reached_new_spoken_text:
            raise PodcastRevisionAddedMaterialUnused(
                "supplemental material must ground at least one new spoken unit"
            )

    recovery = parsed_input.length_recovery_plan
    if (
        "reuse_unused_material" in parsed_input.selected_actions
        and recovery is not None
        and recovery.priority_unused_source_refs
    ):
        priority_keys = {
            _reference_key(reference) for reference in recovery.priority_unused_source_refs
        }
        recovery_material_reached_new_spoken_text = any(
            any(_reference_key(reference) in priority_keys for reference in unit.source_refs)
            and _normalize_required_text(unit.text) not in parent_spoken_texts
            for unit in revised_spoken_units
        )
        if not recovery_material_reached_new_spoken_text:
            raise PodcastRevisionRecoveryMaterialUnused(
                "length recovery must ground at least one new spoken unit in priority material"
            )
    return validated


class DraftRevisionCandidateSummary(BaseModel):
    """Comparable facts about one immutable Draft candidate."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    draft_artifact_id: str
    quality_report_artifact_id: str
    target_duration_minutes: int = Field(gt=0)
    script_character_count: int = Field(ge=0)
    estimated_duration_minutes: float = Field(ge=0)
    duration_coverage_ratio: float = Field(ge=0)
    deterministic_score: int = Field(ge=0, le=100)
    experimental_model_score: float | None = Field(default=None, ge=0, le=100)
    experimental_overall_score: float | None = Field(default=None, ge=0, le=100)
    decision: Literal[
        "blocked",
        "automated_review_incomplete",
        "revision_recommended",
        "candidate_ready_for_human_review",
    ]
    blocker_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)


class DraftRevisionComparison(BaseModel):
    """Text-free, deterministic comparison; the user still chooses the winner."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["draft_revision_comparison_v1"] = DRAFT_REVISION_COMPARISON_VERSION
    parent: DraftRevisionCandidateSummary
    revision: DraftRevisionCandidateSummary
    script_character_delta: int
    estimated_duration_delta_minutes: float
    deterministic_score_delta: int
    experimental_overall_score_delta: float | None
    automatic_winner_selected: Literal[False] = False
    requires_human_review: Literal[True] = True

    @model_validator(mode="after")
    def deltas_must_match_candidates(self) -> DraftRevisionComparison:
        if self.revision.run_id == self.parent.run_id:
            raise ValueError("revision comparison requires two different Runs")
        if self.script_character_delta != (
            self.revision.script_character_count - self.parent.script_character_count
        ):
            raise ValueError("script character delta does not match candidates")
        expected_duration_delta = round(
            self.revision.estimated_duration_minutes - self.parent.estimated_duration_minutes,
            2,
        )
        if self.estimated_duration_delta_minutes != expected_duration_delta:
            raise ValueError("duration delta does not match candidates")
        if self.deterministic_score_delta != (
            self.revision.deterministic_score - self.parent.deterministic_score
        ):
            raise ValueError("deterministic score delta does not match candidates")
        parent_score = self.parent.experimental_overall_score
        revision_score = self.revision.experimental_overall_score
        expected_score_delta = (
            None
            if parent_score is None or revision_score is None
            else round(revision_score - parent_score, 2)
        )
        if self.experimental_overall_score_delta != expected_score_delta:
            raise ValueError("overall score delta does not match candidates")
        return self


class DraftRevisionComparisonRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison: DraftRevisionComparison
    artifact: ArtifactView


def build_draft_revision_candidate_summary(
    *,
    run_id: str,
    draft_artifact_id: str,
    quality_report_artifact_id: str,
    quality_report: DraftQualityReport | dict[str, Any],
) -> DraftRevisionCandidateSummary:
    """Project one immutable quality report into comparable, text-free facts."""

    report = DraftQualityReport.model_validate(quality_report)
    metrics = report.deterministic.metrics
    target_duration = metrics.target_duration_minutes
    return DraftRevisionCandidateSummary(
        run_id=run_id,
        draft_artifact_id=draft_artifact_id,
        quality_report_artifact_id=quality_report_artifact_id,
        target_duration_minutes=target_duration,
        script_character_count=metrics.script_character_count,
        estimated_duration_minutes=metrics.estimated_duration_minutes,
        duration_coverage_ratio=round(
            metrics.estimated_duration_minutes / target_duration,
            4,
        ),
        deterministic_score=report.deterministic.deterministic_score,
        experimental_model_score=report.experimental_model_score,
        experimental_overall_score=report.experimental_overall_score,
        decision=report.decision,
        blocker_count=sum(finding.status == "blocker" for finding in report.deterministic.findings),
        warning_count=sum(finding.status == "warning" for finding in report.deterministic.findings),
    )


def build_draft_revision_comparison(
    *,
    parent: DraftRevisionCandidateSummary,
    revision: DraftRevisionCandidateSummary,
) -> DraftRevisionComparison:
    """Compare candidates without choosing a winner or mutating either one."""

    overall_delta = (
        None
        if (
            parent.experimental_overall_score is None or revision.experimental_overall_score is None
        )
        else round(
            revision.experimental_overall_score - parent.experimental_overall_score,
            2,
        )
    )
    return DraftRevisionComparison(
        parent=parent,
        revision=revision,
        script_character_delta=(revision.script_character_count - parent.script_character_count),
        estimated_duration_delta_minutes=round(
            revision.estimated_duration_minutes - parent.estimated_duration_minutes,
            2,
        ),
        deterministic_score_delta=(revision.deterministic_score - parent.deterministic_score),
        experimental_overall_score_delta=overall_delta,
    )
