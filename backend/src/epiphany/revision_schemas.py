from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from epiphany.schemas import SourceReference

DRAFT_IMPROVEMENT_PLAN_VERSION = "draft_improvement_plan_v1"

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
    """Source inventory without copying any SourceSegment text into the plan."""

    model_config = ConfigDict(extra="forbid")

    total_factual_segment_count: int = Field(ge=0)
    cited_factual_segment_count: int = Field(ge=0)
    unused_factual_segment_count: int = Field(ge=0)
    unused_factual_character_count: int = Field(ge=0)
    unused_source_refs: list[SourceReference] = Field(default_factory=list, max_length=1_000)

    _source_refs_are_unique = field_validator("unused_source_refs")(_unique_references)

    @model_validator(mode="after")
    def counts_must_match_references(self) -> UnusedFactualMaterial:
        if (
            self.cited_factual_segment_count + self.unused_factual_segment_count
            != self.total_factual_segment_count
        ):
            raise ValueError("factual segment counts must add up")
        if self.unused_factual_segment_count != len(self.unused_source_refs):
            raise ValueError("unused count must match unused_source_refs")
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

    schema_version: Literal["draft_improvement_plan_v1"] = DRAFT_IMPROVEMENT_PLAN_VERSION
    parent_run_id: str = Field(min_length=1, max_length=200)
    parent_draft_artifact_id: str = Field(min_length=1, max_length=200)
    quality_report_artifact_id: str = Field(min_length=1, max_length=200)
    writing_style_context_available: bool
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
        if self.duration_resolution == "not_needed":
            if self.duration.missing_script_character_count:
                raise ValueError("not_needed cannot have a duration shortfall")
        elif not self.duration.missing_script_character_count:
            raise ValueError("a duration resolution requires a duration shortfall")
        return self
