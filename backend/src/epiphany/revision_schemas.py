from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from epiphany.draft_feedback_schemas import FeedbackDecision, FeedbackOrigin
from epiphany.draft_quality_schemas import DraftQualityReport
from epiphany.editor_schemas import (
    PodcastDraftOutput,
    PodcastDraftTaskInput,
    validate_podcast_draft_output,
)
from epiphany.schemas import ArtifactView, RunView, SourceReference

DRAFT_IMPROVEMENT_PLAN_VERSION = "draft_improvement_plan_v1"
DRAFT_REVISION_REQUEST_VERSION = "draft_revision_request_v1"
DRAFT_REVISION_COMPARISON_VERSION = "draft_revision_comparison_v1"
REVISE_PODCAST_DRAFT = "revise_podcast_draft"

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


class DraftImprovementPlanRecord(BaseModel):
    """Public JSON view of one persisted deterministic improvement plan."""

    model_config = ConfigDict(extra="forbid")

    plan: DraftImprovementPlan
    artifact: ArtifactView


class CreateDraftRevisionRequest(BaseModel):
    """One explicit, idempotent human decision to create a child Revision Run."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["draft_revision_request_v1"] = DRAFT_REVISION_REQUEST_VERSION
    submission_id: str = Field(min_length=1, max_length=200)
    selected_actions: list[RevisionAction] = Field(min_length=1, max_length=4)
    selected_feedback_artifact_ids: list[str] = Field(default_factory=list, max_length=20)
    selected_gap_codes: list[str] = Field(default_factory=list, max_length=50)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    target_duration_minutes: Literal[10, 15, 30] | None = None
    revision_instruction: str | None = Field(default=None, min_length=1, max_length=2_000)

    _normalize_submission_id = field_validator("submission_id")(_normalize_required_text)
    _normalize_gap_codes = field_validator("selected_gap_codes")(_normalize_unique_required_texts)

    @field_validator(
        "selected_actions",
        "selected_feedback_artifact_ids",
        "source_ids",
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
        if ("lower_target_duration" in actions) != (self.target_duration_minutes is not None):
            raise ValueError("lower_target_duration must match target_duration_minutes")
        return self


class DraftRevisionRequestRecord(BaseModel):
    """Persisted request provenance; the child Run is never created implicitly."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["draft_revision_request_v1"] = DRAFT_REVISION_REQUEST_VERSION
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


class PodcastRevisionTaskInput(PodcastDraftTaskInput):
    """Strict Editor input for one explicit child Revision Run."""

    task_kind: Literal["revise_podcast_draft"]
    parent_run_id: str = Field(min_length=1, max_length=200)
    parent_draft_artifact_id: str = Field(min_length=1, max_length=200)
    parent_quality_report_artifact_id: str = Field(min_length=1, max_length=200)
    plan_artifact_id: str = Field(min_length=1, max_length=200)
    request_artifact_id: str = Field(min_length=1, max_length=200)
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

        validate_podcast_draft_output(
            task_input=revision_base_editor_input(self.model_dump(mode="json")),
            content=self.parent_podcast_draft.model_dump(mode="json"),
        )
        return self


class PodcastRevisionOutputError(ValueError):
    code = "podcast_revision_output_invalid"


class PodcastRevisionNoChange(PodcastRevisionOutputError):
    code = "podcast_revision_no_change"


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
        validated = validate_podcast_draft_output(
            task_input=revision_base_editor_input(task_input),
            content=content,
        )
    except PodcastRevisionOutputError:
        raise
    except (ValueError, TypeError) as error:
        raise PodcastRevisionOutputError(
            "podcast revision did not match the strict task contract"
        ) from error
    if validated == parsed_input.parent_podcast_draft.model_dump(mode="json"):
        raise PodcastRevisionNoChange("podcast revision must produce a changed Draft candidate")
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
