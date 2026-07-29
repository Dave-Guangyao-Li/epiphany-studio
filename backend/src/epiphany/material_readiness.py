from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from epiphany.quality_contract_schemas import (
    DURATION_TOLERANCE_RATIO,
    CreativeBrief,
)
from epiphany.research_schemas import ResearchSourceSegment
from epiphany.schemas import SourceReference

READINESS_METHOD = "deterministic_evidence_volume_v1"
SourceReferenceKey = tuple[str, str]


class ReadinessFollowUpQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=1_000)
    purpose: str = Field(min_length=1, max_length=1_000)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=10)


class MaterialReadinessInput(BaseModel):
    """Ephemeral input for the deterministic readiness calculation.

    This object contains Source text because the calculator must count it. Only
    ``MaterialReadinessReport`` is safe to persist as the readiness Artifact.
    """

    model_config = ConfigDict(extra="forbid")

    creative_brief: CreativeBrief
    initial_source_segments: list[ResearchSourceSegment] = Field(
        min_length=1,
    )
    supplemental_source_segments: list[ResearchSourceSegment] = Field(
        default_factory=list,
    )
    follow_up_questions: list[ReadinessFollowUpQuestion] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def question_references_must_resolve_to_input_segments(
        self,
    ) -> MaterialReadinessInput:
        allowed_keys = {
            _segment_key(segment)
            for segment in [
                *self.initial_source_segments,
                *self.supplemental_source_segments,
            ]
        }
        for question in self.follow_up_questions:
            if any(
                _reference_key(reference) not in allowed_keys for reference in question.source_refs
            ):
                raise ValueError(
                    "follow-up question references must resolve to input source segments"
                )
        return self


class MaterialReadinessCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_source_count: int = Field(ge=0)
    initial_segment_count: int = Field(ge=0)
    initial_char_count: int = Field(ge=0)
    supplemental_source_count: int = Field(ge=0)
    supplemental_segment_count: int = Field(ge=0)
    supplemental_char_count: int = Field(ge=0)
    available_source_char_count: int = Field(ge=0)
    duplicate_segment_count: int = Field(ge=0)


class MaterialReadinessChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_initial_material: bool
    has_supplemental_material: bool
    has_source_diversity: bool
    enough_evidence_chars: bool


ReadinessGapCode = Literal[
    "missing_initial_material",
    "missing_supplemental_material",
    "limited_source_diversity",
    "insufficient_evidence_volume",
]


class MaterialReadinessGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ReadinessGapCode
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=1_000)


class MaterialReadinessReport(BaseModel):
    """Persistable aggregate report that never copies Source text."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "needs_more_material"]
    method: Literal["deterministic_evidence_volume_v1"] = READINESS_METHOD
    target_duration_minutes: int = Field(ge=1)
    speaking_rate_chars_per_minute: int = Field(ge=1)
    duration_tolerance_ratio: float = Field(ge=0, le=1)
    target_script_char_count: int = Field(ge=1)
    target_script_chars_min: int = Field(ge=1)
    target_script_chars_max: int = Field(ge=1)
    estimated_supported_minutes_low: float = Field(ge=0)
    estimated_supported_minutes_high: float = Field(ge=0)
    additional_source_chars_needed: int = Field(ge=0)
    counts: MaterialReadinessCounts
    checks: MaterialReadinessChecks
    gaps: list[MaterialReadinessGap] = Field(default_factory=list, max_length=10)
    follow_up_questions: list[ReadinessFollowUpQuestion] = Field(
        default_factory=list,
        max_length=6,
    )
    limitations: list[str] = Field(min_length=1, max_length=5)


def assess_material_readiness(
    *,
    creative_brief: CreativeBrief | dict[str, Any],
    initial_source_segments: list[ResearchSourceSegment | dict[str, Any]],
    supplemental_source_segments: list[ResearchSourceSegment | dict[str, Any]],
    follow_up_questions: list[ReadinessFollowUpQuestion | dict[str, Any]] | None = None,
) -> MaterialReadinessReport:
    """Estimate whether unique evidence volume can support the target duration.

    This deliberately conservative v1 heuristic treats one non-whitespace
    Source character as at most one supported script character. It does not
    judge narrative quality, factual entailment, voice match, or real recorded
    duration.
    """

    parsed = MaterialReadinessInput.model_validate(
        {
            "creative_brief": creative_brief,
            "initial_source_segments": initial_source_segments,
            "supplemental_source_segments": supplemental_source_segments,
            "follow_up_questions": follow_up_questions or [],
        }
    )
    initial_by_key, initial_duplicate_count = _unique_segments(parsed.initial_source_segments)
    supplemental_by_key, supplemental_duplicate_count = _unique_segments(
        parsed.supplemental_source_segments
    )
    overlapping_keys = set(initial_by_key) & set(supplemental_by_key)
    for key in overlapping_keys:
        supplemental_by_key.pop(key)

    seen_content: set[str] = set()
    initial_segments, initial_content_duplicate_count = _unique_by_content(
        list(initial_by_key.values()),
        seen_content=seen_content,
    )
    supplemental_segments, supplemental_content_duplicate_count = _unique_by_content(
        list(supplemental_by_key.values()),
        seen_content=seen_content,
    )
    initial_char_count = sum(_non_whitespace_char_count(item.text) for item in initial_segments)
    supplemental_char_count = sum(
        _non_whitespace_char_count(item.text) for item in supplemental_segments
    )
    available_char_count = initial_char_count + supplemental_char_count
    duplicate_segment_count = (
        initial_duplicate_count
        + supplemental_duplicate_count
        + len(overlapping_keys)
        + initial_content_duplicate_count
        + supplemental_content_duplicate_count
    )

    brief = parsed.creative_brief
    target_char_count = brief.target_duration_minutes * brief.speaking_rate_chars_per_minute
    tolerance = Decimal(str(DURATION_TOLERANCE_RATIO))
    target_char_decimal = Decimal(target_char_count)
    target_chars_min = math.ceil(target_char_decimal * (Decimal(1) - tolerance))
    target_chars_max = math.floor(target_char_decimal * (Decimal(1) + tolerance))
    supported_minutes = available_char_count / brief.speaking_rate_chars_per_minute
    supported_minutes_low = round(
        supported_minutes * (1 - DURATION_TOLERANCE_RATIO),
        2,
    )
    supported_minutes_high = round(
        supported_minutes * (1 + DURATION_TOLERANCE_RATIO),
        2,
    )

    all_segments = [*initial_segments, *supplemental_segments]
    distinct_source_count = len({segment.source_id for segment in all_segments})
    checks = MaterialReadinessChecks(
        has_initial_material=bool(initial_segments),
        has_supplemental_material=bool(supplemental_segments),
        has_source_diversity=distinct_source_count >= 2,
        enough_evidence_chars=available_char_count >= target_chars_min,
    )
    gaps = _gaps_for(
        checks=checks,
        available_char_count=available_char_count,
        target_chars_min=target_chars_min,
    )
    status = "ready" if not gaps else "needs_more_material"

    return MaterialReadinessReport(
        status=status,
        target_duration_minutes=brief.target_duration_minutes,
        speaking_rate_chars_per_minute=brief.speaking_rate_chars_per_minute,
        duration_tolerance_ratio=DURATION_TOLERANCE_RATIO,
        target_script_char_count=target_char_count,
        target_script_chars_min=target_chars_min,
        target_script_chars_max=target_chars_max,
        estimated_supported_minutes_low=supported_minutes_low,
        estimated_supported_minutes_high=supported_minutes_high,
        additional_source_chars_needed=max(
            0,
            target_chars_min - available_char_count,
        ),
        counts=MaterialReadinessCounts(
            initial_source_count=len({segment.source_id for segment in initial_segments}),
            initial_segment_count=len(initial_segments),
            initial_char_count=initial_char_count,
            supplemental_source_count=len({segment.source_id for segment in supplemental_segments}),
            supplemental_segment_count=len(supplemental_segments),
            supplemental_char_count=supplemental_char_count,
            available_source_char_count=available_char_count,
            duplicate_segment_count=duplicate_segment_count,
        ),
        checks=checks,
        gaps=gaps,
        follow_up_questions=(_unique_questions(parsed.follow_up_questions)[:6] if gaps else []),
        limitations=[
            "本报告只评估去重后的素材字符量、初始与补充材料存在性以及来源多样性。",
            "它不证明叙事质量、事实蕴含、个人声音贴合度或真实录音时长。",
        ],
    )


def _segment_key(segment: ResearchSourceSegment) -> SourceReferenceKey:
    return segment.source_id, segment.source_segment_id


def _reference_key(reference: SourceReference) -> SourceReferenceKey:
    return reference.source_id, reference.source_segment_id


def _unique_segments(
    segments: list[ResearchSourceSegment],
) -> tuple[dict[SourceReferenceKey, ResearchSourceSegment], int]:
    unique: dict[SourceReferenceKey, ResearchSourceSegment] = {}
    duplicate_count = 0
    for segment in segments:
        key = _segment_key(segment)
        if key in unique:
            duplicate_count += 1
            continue
        unique[key] = segment
    return unique, duplicate_count


def _unique_by_content(
    segments: list[ResearchSourceSegment],
    *,
    seen_content: set[str],
) -> tuple[list[ResearchSourceSegment], int]:
    unique: list[ResearchSourceSegment] = []
    duplicate_count = 0
    for segment in segments:
        fingerprint = "".join(segment.text.split())
        if fingerprint in seen_content:
            duplicate_count += 1
            continue
        seen_content.add(fingerprint)
        unique.append(segment)
    return unique, duplicate_count


def _non_whitespace_char_count(text: str) -> int:
    return sum(not character.isspace() for character in text)


def _unique_questions(
    questions: list[ReadinessFollowUpQuestion],
) -> list[ReadinessFollowUpQuestion]:
    unique: list[ReadinessFollowUpQuestion] = []
    seen: set[tuple[str, tuple[SourceReferenceKey, ...]]] = set()
    for question in questions:
        key = (
            question.prompt,
            tuple(_reference_key(reference) for reference in question.source_refs),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(question)
    return unique


def _gaps_for(
    *,
    checks: MaterialReadinessChecks,
    available_char_count: int,
    target_chars_min: int,
) -> list[MaterialReadinessGap]:
    gaps: list[MaterialReadinessGap] = []
    if not checks.has_initial_material:
        gaps.append(
            MaterialReadinessGap(
                code="missing_initial_material",
                title="缺少初始素材",
                detail="至少需要一段初始素材，才能建立可追溯的内容起点。",
            )
        )
    if not checks.has_supplemental_material:
        gaps.append(
            MaterialReadinessGap(
                code="missing_supplemental_material",
                title="缺少补充口述",
                detail="至少需要一段补充口述，才能进入当前 Editor 工作流。",
            )
        )
    if not checks.has_source_diversity:
        gaps.append(
            MaterialReadinessGap(
                code="limited_source_diversity",
                title="来源过于单一",
                detail="目前少于两个独立 Source，无法满足首版来源多样性门槛。",
            )
        )
    if not checks.enough_evidence_chars:
        gaps.append(
            MaterialReadinessGap(
                code="insufficient_evidence_volume",
                title="素材量不足以支撑目标时长",
                detail=(
                    f"当前有 {available_char_count} 个去空白素材字符，"
                    f"保守门槛为 {target_chars_min} 个；"
                    f"还需要至少 {target_chars_min - available_char_count} 个。"
                ),
            )
        )
    return gaps
