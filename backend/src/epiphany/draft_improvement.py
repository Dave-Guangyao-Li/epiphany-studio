from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from epiphany.draft_quality_schemas import DraftQualityReport
from epiphany.editor_schemas import (
    PodcastDraftOutput,
    PodcastDraftTaskInput,
    editor_spoken_script_reference_keys,
    validate_podcast_draft_output,
)
from epiphany.interview_schemas import InterviewScaffoldOutput
from epiphany.quality_contract_schemas import DURATION_TOLERANCE_RATIO
from epiphany.research_schemas import ResearchSourceSegment
from epiphany.revision_schemas import (
    MAX_LENGTH_RECOVERY_PRIORITY_REFS,
    DraftDurationGap,
    DraftImprovementGap,
    DraftImprovementOption,
    DraftImprovementPlan,
    TargetedSupplementQuestion,
    UnusedFactualMaterial,
)
from epiphany.schemas import SourceReference


class DraftImprovementPlanInputError(ValueError):
    code = "draft_improvement_plan_input_invalid"


def _non_whitespace_character_count(value: str) -> int:
    return len("".join(value.split()))


def _script_character_count(draft: PodcastDraftOutput) -> int:
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


def _spoken_script_texts(draft: PodcastDraftOutput) -> list[str]:
    return [
        draft.podcast_script.opening.text,
        *[
            paragraph.text
            for section in draft.podcast_script.sections
            for paragraph in section.paragraphs
        ],
        draft.podcast_script.closing.text,
    ]


def _normalize_overlap_text(value: str) -> str:
    """Normalize conservatively for exact/containment coverage checks."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _select_priority_candidate_segments(
    *,
    unused_segments: Sequence[ResearchSourceSegment],
    draft: PodcastDraftOutput,
    must_include: Sequence[str],
    material_gap_refs: Sequence[SourceReference],
    supplemental_segments: Sequence[ResearchSourceSegment],
) -> list[ResearchSourceSegment]:
    """Return a bounded, deterministic shortlist for one Revision attempt.

    The factual Source contract does not currently carry a ``material_kind`` or
    an editorial/factual classifier, so this function deliberately does not use
    brittle Chinese instruction keywords. It can safely remove exact duplicate
    text and text already copied into the audible draft, then rank the remaining
    factual candidates using traceable signals already present in the contract.
    Post-Revision quality gates still decide whether the attempt was useful.
    """

    spoken_texts = _spoken_script_texts(draft)
    normalized_spoken_units = {
        normalized for text in spoken_texts if (normalized := _normalize_overlap_text(text))
    }
    normalized_spoken_body = _normalize_overlap_text("".join(spoken_texts))
    missing_must_include = [
        normalized
        for item in must_include
        if (normalized := _normalize_overlap_text(item))
        and normalized not in normalized_spoken_body
    ]
    gap_keys = {_reference_key(reference) for reference in material_gap_refs}
    supplemental_keys = {
        (segment.source_id, segment.source_segment_id) for segment in supplemental_segments
    }

    scored: list[tuple[tuple[int, ...], ResearchSourceSegment]] = []
    seen_normalized_texts: set[str] = set()
    for source_order, segment in enumerate(unused_segments):
        normalized = _normalize_overlap_text(segment.text)
        if not normalized or normalized in seen_normalized_texts:
            continue
        seen_normalized_texts.add(normalized)

        # Exact unit reuse is always safe to identify. The longer containment
        # check catches copied source passages while avoiding tiny common
        # fragments that would create false positives.
        if normalized in normalized_spoken_units or (
            len(normalized) >= 16 and normalized in normalized_spoken_body
        ):
            continue

        key = (segment.source_id, segment.source_segment_id)
        must_include_matches = sum(required in normalized for required in missing_must_include)
        numeric_detail_count = sum(
            unicodedata.category(character).startswith("N") for character in segment.text
        )
        punctuation_count = sum(
            unicodedata.category(character).startswith("P") for character in segment.text
        )
        score = (
            must_include_matches,
            int(key in gap_keys),
            int(key in supplemental_keys),
            min(numeric_detail_count, 20),
            min(punctuation_count, 20),
            min(len(normalized), 600),
            -source_order,
        )
        scored.append((score, segment))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [segment for _score, segment in scored[:MAX_LENGTH_RECOVERY_PRIORITY_REFS]]


def _reference_key(reference: SourceReference) -> tuple[str, str]:
    return reference.source_id, reference.source_segment_id


def _unique_source_references(
    references: Sequence[SourceReference],
) -> list[SourceReference]:
    ordered: list[SourceReference] = []
    seen: set[tuple[str, str]] = set()
    for reference in references:
        key = _reference_key(reference)
        if key not in seen:
            ordered.append(reference)
            seen.add(key)
    return ordered


def _lower_duration_preset(target: int) -> int | None:
    if target == 30:
        return 15
    if target == 15:
        return 10
    return None


def _build_targeted_questions(
    scaffold: InterviewScaffoldOutput,
) -> list[TargetedSupplementQuestion]:
    questions: list[TargetedSupplementQuestion] = []

    for gap_index, gap in enumerate(scaffold.material_gaps):
        questions.append(
            TargetedSupplementQuestion(
                prompt=(
                    f"关于“{gap.gap}”，请补充一个具体场景："
                    "当时在哪里、发生了什么、你有什么直接感受？"
                ),
                purpose=gap.why_it_matters,
                anchor_kind="material_gap",
                anchor_path=f"material_gaps[{gap_index}]",
                anchor_text=gap.gap,
                keywords=[],
                source_refs=gap.source_refs,
            )
        )
        if len(questions) == 6:
            return questions

    scaffold_question_anchors: list[TargetedSupplementQuestion] = []
    for section_index, section in enumerate(scaffold.sections):
        for question_index, question in enumerate(section.questions):
            scaffold_question_anchors.append(
                TargetedSupplementQuestion(
                    prompt=question.prompt,
                    purpose=question.purpose,
                    anchor_kind="scaffold_question",
                    anchor_path=(f"sections[{section_index}].questions[{question_index}]"),
                    anchor_text=question.prompt,
                    keywords=question.keywords,
                    source_refs=question.source_refs,
                )
            )

    for question in scaffold_question_anchors:
        if len(questions) == 6:
            break
        if (question.anchor_path, question.prompt) not in {
            (existing.anchor_path, existing.prompt) for existing in questions
        }:
            questions.append(question)

    # The strict Interview Scaffold permits two sections with one question each.
    # Derive additional prompts from those same user-facing questions so every
    # plan offers at least three useful choices without inventing a new topic.
    fallback_prompts = (
        (
            "请围绕这个问题，再补充一个听众可以看见或听见的具体细节。",
            "把已有问题从抽象结论推进到可口述的场景。",
        ),
        (
            "请围绕这个问题，说说当时的理解和现在的理解有什么变化。",
            "补充时间前后的认知变化，避免只陈述结论。",
        ),
    )
    for base_question in scaffold_question_anchors:
        for prompt, purpose in fallback_prompts:
            if len(questions) >= 3:
                break
            questions.append(
                TargetedSupplementQuestion(
                    prompt=prompt,
                    purpose=purpose,
                    anchor_kind="scaffold_question",
                    anchor_path=base_question.anchor_path,
                    anchor_text=base_question.anchor_text,
                    keywords=base_question.keywords,
                    source_refs=base_question.source_refs,
                )
            )
        if len(questions) >= 3:
            break
    return questions[:6]


def _quality_gaps(report: DraftQualityReport) -> list[DraftImprovementGap]:
    gaps: list[DraftImprovementGap] = []
    for finding in report.deterministic.findings:
        if finding.status not in {"warning", "blocker"}:
            continue
        gaps.append(
            DraftImprovementGap(
                code=finding.code,
                kind=(
                    "duration_shortfall"
                    if finding.code == "draft.empty" or finding.code.startswith("duration.")
                    else "deterministic_quality"
                ),
                severity=finding.status,
                explanation=(
                    "口播正文时长未达到 Creative Brief 的目标。"
                    if finding.code == "draft.empty" or finding.code.startswith("duration.")
                    else "确定性质量规则发现了需要修改的问题。"
                ),
            )
        )

    if report.model_self_review is not None:
        for dimension in report.model_self_review.dimensions:
            if dimension.assessable and dimension.score is not None and dimension.score <= 2:
                gaps.append(
                    DraftImprovementGap(
                        code=f"model_review.{dimension.dimension}",
                        kind="model_review",
                        severity="warning",
                        explanation="模型 Reviewer 给这一维度的建议分数不高。",
                    )
                )
    return gaps


def build_draft_improvement_plan(
    *,
    parent_run_id: str,
    parent_draft_artifact_id: str,
    quality_report_artifact_id: str,
    editor_task_input: dict[str, Any],
    podcast_draft: PodcastDraftOutput | dict[str, Any],
    quality_report: DraftQualityReport | dict[str, Any],
    interview_scaffold: InterviewScaffoldOutput | dict[str, Any],
    writing_style_context_available: bool,
    selected_feedback_codes: Sequence[str] = (),
) -> DraftImprovementPlan:
    """Build a deterministic revision plan without calling a model.

    SourceSegment text is used only for exact character accounting. It is never
    copied into the returned plan; the plan carries stable SourceReferences.
    """

    try:
        parsed_input = PodcastDraftTaskInput.model_validate(editor_task_input)
        parsed_draft = PodcastDraftOutput.model_validate(podcast_draft)
        parsed_report = DraftQualityReport.model_validate(quality_report)
        parsed_scaffold = InterviewScaffoldOutput.model_validate(interview_scaffold)
        normalized_feedback_codes = [" ".join(code.split()) for code in selected_feedback_codes]
        if any(not code for code in normalized_feedback_codes):
            raise ValueError("selected_feedback_codes must not contain blanks")
        if len(normalized_feedback_codes) != len(set(normalized_feedback_codes)):
            raise ValueError("selected_feedback_codes must be unique")
        if parsed_input.creative_brief is None:
            raise ValueError("Editor task input must contain a Creative Brief")
        if parsed_input.interview_scaffold.model_dump(mode="json") != parsed_scaffold.model_dump(
            mode="json"
        ):
            raise ValueError("interview scaffold must match the Editor task input")
        validate_podcast_draft_output(
            task_input=parsed_input.model_dump(mode="json"),
            content=parsed_draft.model_dump(mode="json"),
        )
    except (ValidationError, ValueError, TypeError) as error:
        raise DraftImprovementPlanInputError(
            "could not build a plan from inconsistent workflow artifacts"
        ) from error

    brief = parsed_input.creative_brief
    metrics = parsed_report.deterministic.metrics
    actual_characters = _script_character_count(parsed_draft)
    if (
        metrics.target_duration_minutes != brief.target_duration_minutes
        or metrics.speaking_rate_chars_per_minute != brief.speaking_rate_chars_per_minute
        or metrics.script_character_count != actual_characters
    ):
        raise DraftImprovementPlanInputError(
            "quality report does not describe the Editor draft and Creative Brief"
        )

    target_characters = brief.target_duration_minutes * brief.speaking_rate_chars_per_minute
    missing_characters = max(0, target_characters - actual_characters)
    minimum_characters = math.ceil(
        Decimal(target_characters) * (Decimal(1) - Decimal(str(DURATION_TOLERANCE_RATIO)))
    )
    missing_to_minimum_characters = max(
        0,
        minimum_characters - actual_characters,
    )
    estimated_minutes = round(
        actual_characters / brief.speaking_rate_chars_per_minute,
        2,
    )
    duration = DraftDurationGap(
        target_duration_minutes=brief.target_duration_minutes,
        speaking_rate_chars_per_minute=brief.speaking_rate_chars_per_minute,
        target_script_character_count=target_characters,
        actual_script_character_count=actual_characters,
        estimated_duration_minutes=estimated_minutes,
        duration_coverage_ratio=round(
            estimated_minutes / brief.target_duration_minutes,
            4,
        ),
        missing_script_character_count=missing_characters,
        missing_duration_minutes=round(
            missing_characters / brief.speaking_rate_chars_per_minute,
            2,
        ),
    )

    factual_segments = [
        *parsed_input.initial_source_segments,
        *parsed_input.supplemental_source_segments,
    ]
    cited_keys = set(editor_spoken_script_reference_keys(parsed_draft.model_dump(mode="json")))
    unused_segments = [
        segment
        for segment in factual_segments
        if (segment.source_id, segment.source_segment_id) not in cited_keys
    ]
    unused_refs = [
        SourceReference(
            source_id=segment.source_id,
            source_segment_id=segment.source_segment_id,
        )
        for segment in unused_segments
    ]
    unused_characters = sum(
        _non_whitespace_character_count(segment.text) for segment in unused_segments
    )
    priority_segments = _select_priority_candidate_segments(
        unused_segments=unused_segments,
        draft=parsed_draft,
        must_include=brief.must_include,
        material_gap_refs=[
            reference for gap in parsed_scaffold.material_gaps for reference in gap.source_refs
        ],
        supplemental_segments=parsed_input.supplemental_source_segments,
    )
    priority_refs = [
        SourceReference(
            source_id=segment.source_id,
            source_segment_id=segment.source_segment_id,
        )
        for segment in priority_segments
    ]
    priority_characters = sum(
        _non_whitespace_character_count(segment.text) for segment in priority_segments
    )
    total_keys = {(segment.source_id, segment.source_segment_id) for segment in factual_segments}
    cited_factual_count = len(total_keys & cited_keys)
    material = UnusedFactualMaterial(
        total_factual_segment_count=len(factual_segments),
        cited_factual_segment_count=cited_factual_count,
        unused_factual_segment_count=len(unused_segments),
        unused_factual_character_count=unused_characters,
        unused_source_refs=unused_refs,
        priority_candidates_assessed=True,
        priority_candidate_character_count=priority_characters,
        priority_candidate_source_refs=priority_refs,
    )

    if not missing_to_minimum_characters:
        resolution = "not_needed"
    elif priority_characters >= missing_to_minimum_characters:
        resolution = "reuse_unused_material"
    elif priority_characters:
        resolution = "reuse_then_supplement"
    else:
        resolution = "add_supplemental_material"

    gaps = _quality_gaps(parsed_report)
    existing_gap_codes = {gap.code for gap in gaps}
    for gap_index, scaffold_gap in enumerate(parsed_scaffold.material_gaps):
        code = f"scaffold.material_gap.{gap_index}"
        if code not in existing_gap_codes:
            gaps.append(
                DraftImprovementGap(
                    code=code,
                    kind="scaffold_material_gap",
                    severity="warning",
                    explanation=(f"{scaffold_gap.gap} 原因：{scaffold_gap.why_it_matters}"),
                    source_refs=scaffold_gap.source_refs,
                )
            )
    for feedback_code in normalized_feedback_codes:
        gaps.append(
            DraftImprovementGap(
                code=f"selected_feedback.{feedback_code}",
                kind="selected_feedback",
                severity="warning",
                explanation="用户已选择将这条反馈应用到下一版草稿。",
            )
        )

    options: list[DraftImprovementOption] = []
    if missing_to_minimum_characters and priority_segments:
        candidate_volume_supports_attempt = priority_characters >= missing_to_minimum_characters
        options.append(
            DraftImprovementOption(
                kind="reuse_unused_material",
                recommended=candidate_volume_supports_attempt,
                explanation=(
                    (
                        "已筛选的候选事实片段，其原始字数足以支持一次受控扩写尝试；"
                        "这不保证修改后一定达到时长，仍需检查信息增量、重复与口播质量。"
                    )
                    if candidate_volume_supports_attempt
                    else (
                        "可以先复用已筛选的候选事实片段增加具体内容，但其原始字数"
                        "仍低于当前时长缺口；扩写后预计还需要补充材料或降低目标时长。"
                    )
                ),
                source_refs=priority_refs,
            )
        )
    if missing_to_minimum_characters > priority_characters or bool(parsed_scaffold.material_gaps):
        options.append(
            DraftImprovementOption(
                kind="add_supplemental_material",
                recommended=missing_to_minimum_characters > priority_characters,
                explanation=(
                    "筛选后的候选材料原始字数不足以支持一次完整扩写，或采访脚手架仍有明确材料缺口。"
                ),
                source_refs=_unique_source_references(
                    [
                        reference
                        for gap in parsed_scaffold.material_gaps
                        for reference in gap.source_refs
                    ]
                ),
            )
        )
    lower_preset = _lower_duration_preset(brief.target_duration_minutes)
    if missing_to_minimum_characters and lower_preset is not None:
        options.append(
            DraftImprovementOption(
                kind="lower_target_duration",
                recommended=False,
                explanation="如果不想补充材料，可以选择更低的预设口播时长。",
                suggested_target_duration_minutes=lower_preset,
            )
        )
    if normalized_feedback_codes:
        options.append(
            DraftImprovementOption(
                kind="apply_selected_feedback",
                recommended=True,
                explanation="在保留原稿和反馈追踪记录的前提下应用已选择的反馈。",
            )
        )

    try:
        return DraftImprovementPlan(
            parent_run_id=parent_run_id,
            parent_draft_artifact_id=parent_draft_artifact_id,
            quality_report_artifact_id=quality_report_artifact_id,
            writing_style_context_available=writing_style_context_available,
            selected_feedback_codes=normalized_feedback_codes,
            duration=duration,
            material=material,
            duration_resolution=resolution,
            gaps=gaps,
            options=options,
            targeted_questions=_build_targeted_questions(parsed_scaffold),
        )
    except (ValidationError, ValueError, TypeError) as error:
        raise DraftImprovementPlanInputError(
            "generated improvement plan did not match its strict schema"
        ) from error
