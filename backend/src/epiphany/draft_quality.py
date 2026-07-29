from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from epiphany.draft_quality_schemas import (
    DRAFT_QUALITY_FORMULA_VERSION,
    REVIEW_DIMENSIONS,
    DeterministicDraftMetrics,
    DeterministicDraftQualityResult,
    DraftQualityFinding,
    DraftQualityReport,
    ModelSelfReviewOutput,
    ReviewerRelation,
)
from epiphany.editor_schemas import PodcastDraftOutput
from epiphany.quality_contract_schemas import (
    DURATION_TOLERANCE_RATIO,
    CreativeBrief,
    DraftQualityConfig,
)

_REPEATED_WINDOW_SIZE = 8
_REPEATED_WINDOW_WARNING_RATIO = 0.12
_SEVERE_DURATION_LOWER_RATIO = 0.50
_SEVERE_DURATION_UPPER_RATIO = 1.50
_FILLER_WARNING_RATIO = 0.02
_TEMPLATE_WARNING_COUNT = 2
_NOT_BUT_WARNING_COUNT = 2

_FILLER_PATTERNS: tuple[str, ...] = (
    "嗯",
    "呃",
    "就是",
    "其实",
    "然后",
    "怎么说呢",
    "某种程度上",
    "我觉得",
)
_TEMPLATE_PATTERNS: tuple[str, ...] = (
    "值得注意的是",
    "总而言之",
    "归根结底",
    "在这个快节奏的时代",
    "让我们一起",
    "不禁让人",
    "毋庸置疑",
    "不可否认",
)
_NOT_BUT_PATTERN = re.compile(r"不是[^。！？\n]{0,40}而是")


def _non_whitespace(value: str) -> str:
    return "".join(value.split())


def _normalized_paragraph(value: str) -> str:
    return _non_whitespace(unicodedata.normalize("NFKC", value)).casefold()


def _draft_mapping(draft: PodcastDraftOutput | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(draft, PodcastDraftOutput):
        return draft.model_dump(mode="python")
    return draft


def _script_paragraphs(
    draft: PodcastDraftOutput | Mapping[str, Any],
) -> list[tuple[str, str, list[Mapping[str, Any]]]]:
    """Extract script prose without requiring a valid Pydantic object.

    The Editor normally supplies a strict ``PodcastDraftOutput``. Supporting a
    mapping here lets the quality gate turn a malformed/missing citation into a
    blocker instead of crashing before it can explain the problem.
    """

    content = _draft_mapping(draft)
    script = content.get("podcast_script")
    if not isinstance(script, Mapping):
        return []

    extracted: list[tuple[str, str, list[Mapping[str, Any]]]] = []

    def add(location: str, paragraph: object) -> None:
        if not isinstance(paragraph, Mapping):
            return
        text = paragraph.get("text")
        if not isinstance(text, str):
            text = ""
        references = paragraph.get("source_refs")
        valid_references = (
            [reference for reference in references if isinstance(reference, Mapping)]
            if isinstance(references, list)
            else []
        )
        extracted.append((location, text, valid_references))

    add("podcast_script.opening", script.get("opening"))
    sections = script.get("sections")
    if isinstance(sections, list):
        for section_index, section in enumerate(sections):
            if not isinstance(section, Mapping):
                continue
            paragraphs = section.get("paragraphs")
            if not isinstance(paragraphs, list):
                continue
            for paragraph_index, paragraph in enumerate(paragraphs):
                add(
                    (f"podcast_script.sections[{section_index}].paragraphs[{paragraph_index}]"),
                    paragraph,
                )
    add("podcast_script.closing", script.get("closing"))
    return extracted


def _finding(
    code: str,
    status: str,
    *,
    location: str,
    exact_quote: str = "",
    observed: int | float | str | bool,
    threshold: int | float | str | bool,
) -> DraftQualityFinding:
    return DraftQualityFinding(
        code=code,
        status=status,
        location=location,
        exact_quote=exact_quote[:500],
        observed=observed,
        threshold=threshold,
    )


def _count_literal_patterns(text: str, patterns: Iterable[str]) -> tuple[int, str]:
    total = 0
    first_match = ""
    for pattern in patterns:
        count = text.count(pattern)
        total += count
        if count and not first_match:
            first_match = pattern
    return total, first_match


def _repeated_window_ratio(paragraph_texts: Iterable[str]) -> tuple[float, str]:
    windows: list[str] = []
    for text in paragraph_texts:
        normalized = _normalized_paragraph(text)
        windows.extend(
            normalized[index : index + _REPEATED_WINDOW_SIZE]
            for index in range(max(0, len(normalized) - _REPEATED_WINDOW_SIZE + 1))
        )
    if not windows:
        return 0.0, ""
    counts = Counter(windows)
    repeated_surplus = sum(count - 1 for count in counts.values() if count > 1)
    repeated = next((window for window in windows if counts[window] > 1), "")
    return repeated_surplus / len(windows), repeated


def analyze_podcast_draft(
    *,
    draft: PodcastDraftOutput | Mapping[str, Any],
    creative_brief: CreativeBrief | Mapping[str, Any],
    config: DraftQualityConfig | Mapping[str, Any] | None = None,
) -> DeterministicDraftQualityResult:
    """Run explainable, deterministic checks over the spoken script.

    This function deliberately reports observable patterns. It does not claim
    to detect whether text was written by AI.
    """

    parsed_config = DraftQualityConfig.model_validate(config or {})
    if not parsed_config.enabled:
        raise ValueError("draft quality analysis is disabled")
    brief = CreativeBrief.model_validate(creative_brief)
    paragraphs = _script_paragraphs(draft)
    paragraph_texts = [text for _, text, _ in paragraphs]
    script_text = "\n".join(paragraph_texts)
    normalized_script = _non_whitespace(script_text)
    character_count = len(normalized_script)
    estimated_minutes = (
        character_count / brief.speaking_rate_chars_per_minute
        if brief.speaking_rate_chars_per_minute
        else 0
    )
    target = brief.target_duration_minutes
    normal_lower = target * (1 - DURATION_TOLERANCE_RATIO)
    normal_upper = target * (1 + DURATION_TOLERANCE_RATIO)
    severe_lower = target * _SEVERE_DURATION_LOWER_RATIO
    severe_upper = target * _SEVERE_DURATION_UPPER_RATIO

    findings: list[DraftQualityFinding] = []
    penalties = 0

    if character_count == 0:
        duration_status = "blocker"
        duration_code = "draft.empty"
        penalties += 100
    elif estimated_minutes < severe_lower or estimated_minutes > severe_upper:
        duration_status = "blocker"
        duration_code = "duration.severe_deviation"
        penalties += 35
    elif estimated_minutes < normal_lower or estimated_minutes > normal_upper:
        duration_status = "warning"
        duration_code = "duration.outside_target_range"
        penalties += 10
    else:
        duration_status = "pass"
        duration_code = "duration.within_target_range"
    findings.append(
        _finding(
            duration_code,
            duration_status,
            location="podcast_script",
            exact_quote=next((text for text in paragraph_texts if text.strip()), ""),
            observed=round(estimated_minutes, 2),
            threshold=f"{normal_lower:.2f}-{normal_upper:.2f} minutes",
        )
    )

    paragraph_count = len(paragraphs)
    cited_count = sum(bool(references) for _, _, references in paragraphs)
    citation_coverage = cited_count / paragraph_count if paragraph_count else 0.0
    missing_citation = next(
        ((location, text) for location, text, references in paragraphs if not references),
        ("podcast_script", ""),
    )
    citation_status = "pass" if paragraph_count and cited_count == paragraph_count else "blocker"
    if citation_status == "blocker":
        penalties += 40
    findings.append(
        _finding(
            "citations.paragraph_coverage",
            citation_status,
            location=missing_citation[0],
            exact_quote=missing_citation[1],
            observed=round(citation_coverage, 4),
            threshold=1.0,
        )
    )

    reference_keys = {
        (
            str(reference.get("source_id", "")),
            str(reference.get("source_segment_id", "")),
        )
        for _, _, references in paragraphs
        for reference in references
        if reference.get("source_id") and reference.get("source_segment_id")
    }
    source_ids = {source_id for source_id, _ in reference_keys}
    findings.append(
        _finding(
            "citations.source_diversity",
            "pass",
            location="podcast_script",
            observed=f"{len(source_ids)} sources / {len(reference_keys)} segments",
            threshold="reported only; no quality threshold",
        )
    )

    normalized_locations: dict[str, list[tuple[str, str]]] = {}
    for location, text, _ in paragraphs:
        normalized = _normalized_paragraph(text)
        if normalized:
            normalized_locations.setdefault(normalized, []).append((location, text))
    duplicate_groups = [group for group in normalized_locations.values() if len(group) > 1]
    duplicate_count = sum(len(group) - 1 for group in duplicate_groups)
    duplicate_example = duplicate_groups[0][1] if duplicate_groups else ("podcast_script", "")
    duplicate_status = "warning" if duplicate_count else "pass"
    if duplicate_count:
        penalties += min(20, duplicate_count * 5)
    findings.append(
        _finding(
            "repetition.exact_normalized_paragraphs",
            duplicate_status,
            location=duplicate_example[0],
            exact_quote=duplicate_example[1],
            observed=duplicate_count,
            threshold=0,
        )
    )

    repeated_ratio, repeated_window = _repeated_window_ratio(paragraph_texts)
    repeated_status = "warning" if repeated_ratio >= _REPEATED_WINDOW_WARNING_RATIO else "pass"
    if repeated_status == "warning":
        penalties += 10
    findings.append(
        _finding(
            "repetition.eight_character_windows",
            repeated_status,
            location="podcast_script",
            exact_quote=repeated_window,
            observed=round(repeated_ratio, 4),
            threshold=_REPEATED_WINDOW_WARNING_RATIO,
        )
    )

    normalized_for_literals = unicodedata.normalize("NFKC", normalized_script).casefold()
    missing_must_include = [
        item
        for item in brief.must_include
        if _normalized_paragraph(item) not in normalized_for_literals
    ]
    must_include_status = "warning" if missing_must_include else "pass"
    if missing_must_include:
        penalties += min(16, len(missing_must_include) * 4)
    findings.append(
        _finding(
            "brief.must_include",
            must_include_status,
            location="creative_brief.must_include",
            exact_quote=missing_must_include[0] if missing_must_include else "",
            observed="、".join(missing_must_include) if missing_must_include else 0,
            threshold="all literal items present",
        )
    )

    avoid_hits: list[tuple[str, int]] = []
    for pattern in brief.avoid_patterns:
        count = normalized_for_literals.count(_normalized_paragraph(pattern))
        if count:
            avoid_hits.append((pattern, count))
    avoid_count = sum(count for _, count in avoid_hits)
    avoid_status = "warning" if avoid_count else "pass"
    if avoid_count:
        penalties += min(16, avoid_count * 4)
    findings.append(
        _finding(
            "brief.avoid_patterns",
            avoid_status,
            location="podcast_script",
            exact_quote=avoid_hits[0][0] if avoid_hits else "",
            observed=avoid_count,
            threshold=0,
        )
    )

    filler_count, filler_example = _count_literal_patterns(normalized_script, _FILLER_PATTERNS)
    filler_ratio = filler_count / max(1, character_count)
    filler_density = filler_ratio * 1_000
    filler_status = "warning" if filler_ratio > _FILLER_WARNING_RATIO else "pass"
    if filler_status == "warning":
        penalties += 8
    findings.append(
        _finding(
            "style.filler_phrases",
            filler_status,
            location="podcast_script",
            exact_quote=filler_example,
            observed=round(filler_density, 2),
            threshold=f"<= {_FILLER_WARNING_RATIO * 1_000:.2f} per 1000 chars",
        )
    )

    template_count, template_example = _count_literal_patterns(
        normalized_script, _TEMPLATE_PATTERNS
    )
    template_status = "warning" if template_count > _TEMPLATE_WARNING_COUNT else "pass"
    if template_status == "warning":
        penalties += min(12, (template_count - _TEMPLATE_WARNING_COUNT) * 3)
    findings.append(
        _finding(
            "style.template_phrases",
            template_status,
            location="podcast_script",
            exact_quote=template_example,
            observed=template_count,
            threshold=f"<= {_TEMPLATE_WARNING_COUNT}",
        )
    )

    not_but_matches = list(_NOT_BUT_PATTERN.finditer(script_text))
    not_but_count = len(not_but_matches)
    not_but_status = "warning" if not_but_count > _NOT_BUT_WARNING_COUNT else "pass"
    if not_but_status == "warning":
        penalties += min(9, (not_but_count - _NOT_BUT_WARNING_COUNT) * 3)
    findings.append(
        _finding(
            "style.not_but_pattern",
            not_but_status,
            location="podcast_script",
            exact_quote=not_but_matches[0].group(0) if not_but_matches else "",
            observed=not_but_count,
            threshold=f"<= {_NOT_BUT_WARNING_COUNT}",
        )
    )

    metrics = DeterministicDraftMetrics(
        target_duration_minutes=target,
        estimated_duration_minutes=round(estimated_minutes, 2),
        speaking_rate_chars_per_minute=brief.speaking_rate_chars_per_minute,
        script_character_count=character_count,
        paragraph_count=paragraph_count,
        cited_paragraph_count=cited_count,
        paragraph_citation_coverage=round(citation_coverage, 4),
        unique_source_count=len(source_ids),
        unique_segment_count=len(reference_keys),
        exact_duplicate_paragraph_count=duplicate_count,
        repeated_eight_character_window_ratio=round(repeated_ratio, 4),
        must_include_missing_count=len(missing_must_include),
        avoid_pattern_hit_count=avoid_count,
        filler_phrase_count=filler_count,
        filler_phrase_density_per_1000_chars=round(filler_density, 2),
        template_phrase_count=template_count,
        not_but_pattern_count=not_but_count,
    )
    return DeterministicDraftQualityResult(
        profile=parsed_config.profile,
        deterministic_score=max(0, 100 - penalties),
        metrics=metrics,
        findings=findings,
    )


def _reviewer_relation(
    *,
    editor_provider: str | None,
    editor_model: str | None,
    reviewer_provider: str | None,
    reviewer_model: str | None,
) -> ReviewerRelation:
    identities = (editor_provider, editor_model, reviewer_provider, reviewer_model)
    if any(not identity for identity in identities):
        return "unknown"
    if (editor_provider, editor_model) == (reviewer_provider, reviewer_model):
        return "same_model"
    return "different_model"


def _experimental_model_score(review: ModelSelfReviewOutput) -> float | None:
    ordered = {dimension.dimension: dimension for dimension in review.dimensions}
    scores = [
        ordered[name].score
        for name in REVIEW_DIMENSIONS
        if ordered[name].assessable and ordered[name].score is not None
    ]
    # A partial set of model dimensions is still useful as cards, but folding
    # it into one number would hide what the model admitted it could not judge.
    if len(scores) != len(REVIEW_DIMENSIONS):
        return None
    return round(sum(scores) / len(scores) / 5 * 100, 2)


def build_draft_quality_report(
    *,
    deterministic: DeterministicDraftQualityResult,
    model_self_review: ModelSelfReviewOutput | Mapping[str, Any] | None,
    editor_provider: str | None = None,
    editor_model: str | None = None,
    reviewer_provider: str | None = None,
    reviewer_model: str | None = None,
    unavailable_reason: str | None = None,
) -> DraftQualityReport:
    """Combine reviews without allowing a model to erase objective blockers."""

    parsed_review = (
        None
        if model_self_review is None
        else ModelSelfReviewOutput.model_validate(model_self_review)
    )
    model_score = None if parsed_review is None else _experimental_model_score(parsed_review)
    overall_score = (
        None
        if model_score is None
        else round(0.6 * deterministic.deterministic_score + 0.4 * model_score, 2)
    )

    if deterministic.has_blocker:
        decision = "blocked"
    elif parsed_review is None or model_score is None:
        decision = "automated_review_incomplete"
    else:
        low_model_dimension = any(
            dimension.assessable and dimension.score is not None and dimension.score <= 2
            for dimension in parsed_review.dimensions
        )
        decision = (
            "revision_recommended"
            if deterministic.has_warning or low_model_dimension
            else "candidate_ready_for_human_review"
        )

    return DraftQualityReport(
        profile=deterministic.profile,
        deterministic=deterministic,
        model_self_review=parsed_review,
        model_review_status=("unavailable" if parsed_review is None else "completed"),
        model_review_unavailable_reason=(
            (unavailable_reason or "model_self_review_unavailable")
            if parsed_review is None
            else None
        ),
        reviewer_relation=(
            None
            if parsed_review is None
            else _reviewer_relation(
                editor_provider=editor_provider,
                editor_model=editor_model,
                reviewer_provider=reviewer_provider,
                reviewer_model=reviewer_model,
            )
        ),
        scoring_formula_version=DRAFT_QUALITY_FORMULA_VERSION,
        experimental_model_score=model_score,
        experimental_overall_score=overall_score,
        decision=decision,
    )
