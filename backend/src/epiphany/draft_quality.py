from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from epiphany.draft_quality_schemas import (
    CHINESE_STYLE_HEURISTIC_VERSION,
    DETERMINISTIC_QUALITY_FACTS_VERSION,
    DRAFT_QUALITY_FORMULA_VERSION,
    DRAFT_QUALITY_RULES_VERSION,
    LEGACY_DETERMINISTIC_QUALITY_FACTS_VERSION,
    LEGACY_DRAFT_QUALITY_FORMULA_VERSION,
    LEGACY_DRAFT_QUALITY_RULES_VERSION,
    PREVIOUS_CHINESE_STYLE_HEURISTIC_VERSION,
    PREVIOUS_DRAFT_QUALITY_RULES_VERSION,
    STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION,
    ChineseStylePatternCounts,
    DeterministicDraftMetrics,
    DeterministicDraftQualityResult,
    DeterministicQualityFacts,
    DraftQualityFinding,
    DraftQualityReport,
    ModelReviewConflict,
    ModelSelfReviewOutput,
    QualityScoreCapReason,
    ReviewerRelation,
    WritingStyleContextStatus,
    calculate_model_review_score,
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
_MIN_SENTENCE_CV_SAMPLE = 6
_MIN_PARAGRAPH_CV_SAMPLE = 4
_SENTENCE_LENGTH_CV_WARNING_BELOW = 0.12
_PARAGRAPH_LENGTH_CV_WARNING_BELOW = 0.10

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
_EDITORIAL_INSTRUCTION_PATTERN = re.compile(
    r"(?:需要|应该|最好)在(?:口播|正文|稿子|节目)(?:里|中)?"
    r"(?:解释|交代|补充|加入|放)"
    r"|如果(?:这篇|这段|这份)?(?:口播|正文|稿子|节目)(?:还)?"
    r"需要(?:更长|扩写|展开)"
    r"|(?:这句话|这一段|这段|这个表达)[^。！？!?\n]{0,48}"
    r"(?:如果要用|要改|需要改|不适合直接说)"
    r"|(?:前面|后面)[^。！？!?\n]{0,24}(?:一定要|需要|最好)"
    r"(?:先|再)?(?:放|写|补)"
)
_PREVIOUS_ENUMERATION_PATTERN = re.compile(
    r"首先|其次|再次|最后|第[一二三四五六七八九十][，、,:：]"
)

# These patterns describe observable Chinese podcast-writing habits. They are
# deliberately versioned and conservative: a hit is not proof that AI wrote
# the text, and only repeated use crosses a warning threshold.
_CHINESE_STYLE_PATTERNS: dict[str, re.Pattern[str]] = {
    "parallel_contrast": re.compile(
        r"(?:(?:并非|并不是|不是)[^。！？!?\n]{0,48}(?:而是|只是))"
        r"|(?:与其[^。！？!?\n]{0,32}不如)"
    ),
    "escalation": re.compile(
        r"(?:(?:不只|不只是|不仅)[^。！？!?\n]{0,40}(?:还|也|更|而且|更是))"
        r"|(?:从[^。！？!?\n]{0,20}到[^。！？!?\n]{0,20}(?:再到|最后到))"
    ),
    "enumeration": re.compile(
        r"(?:首先|其次|再次|最后)[，、,:：]|第[一二三四五六七八九十][，、,:：]"
    ),
    "generic_transition": re.compile(
        r"值得注意的是|总而言之|归根结底|换句话说|与此同时|"
        r"除此之外|此外|由此可见|不可否认|毋庸置疑"
    ),
    "generic_epiphany": re.compile(
        r"我(?:突然|这才|终于)?(?:意识到|明白了?|发现)|"
        r"原来[^。！？!?\n]{0,16}(?:才是|就是|并不是)|"
        r"真正(?:重要|关键)的是"
    ),
    "generic_coda": re.compile(
        r"让我们一起|"
        r"如果你也[^。！？!?\n]{0,40}(?:希望|愿意|愿|不妨|可以)|"
        r"希望(?:今天|这期|这一期)[^。！？!?\n]{0,40}(?:帮助|陪伴|启发)|"
        r"感谢(?:大家|你|您的?)的?(?:收听|聆听)|"
        r"我们(?:下期|下一期)再见|以上就是"
    ),
    "over_polite": re.compile(
        r"非常荣幸|请允许我|衷心感谢|诚挚地|敬请|"
        r"感谢您的?耐心|欢迎各位|尊敬的(?:听众|朋友|来宾)"
    ),
}
_CHINESE_STYLE_WARNING_MINIMUMS: dict[str, int] = {
    "parallel_contrast": 3,
    "escalation": 3,
    "enumeration": 3,
    "generic_transition": 3,
    "generic_epiphany": 3,
    "generic_coda": 3,
    "over_polite": 2,
}
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"[。！？!?；;]+|…{2,}")


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


def _chinese_style_observations(
    paragraphs: Iterable[tuple[str, str, list[Mapping[str, Any]]]],
    *,
    patterns: Mapping[str, re.Pattern[str]] = _CHINESE_STYLE_PATTERNS,
) -> dict[str, tuple[int, str, str]]:
    observations: dict[str, tuple[int, str, str]] = {}
    paragraph_list = list(paragraphs)
    for category, pattern in patterns.items():
        count = 0
        first_location = "podcast_script"
        first_quote = ""
        for location, text, _ in paragraph_list:
            matches = list(pattern.finditer(text))
            count += len(matches)
            if matches and not first_quote:
                first_location = location
                first_quote = matches[0].group(0)
        observations[category] = (count, first_location, first_quote)
    return observations


def _spoken_sentence_lengths(paragraph_texts: Iterable[str]) -> list[int]:
    lengths: list[int] = []
    for text in paragraph_texts:
        for sentence in _SENTENCE_BOUNDARY_PATTERN.split(text):
            length = len(_non_whitespace(sentence))
            if length:
                lengths.append(length)
    return lengths


def _coefficient_of_variation(lengths: list[int], *, minimum_sample: int) -> float | None:
    if len(lengths) < minimum_sample:
        return None
    mean = statistics.fmean(lengths)
    if mean == 0:
        return None
    return statistics.pstdev(lengths) / mean


def analyze_podcast_draft(
    *,
    draft: PodcastDraftOutput | Mapping[str, Any],
    creative_brief: CreativeBrief | Mapping[str, Any],
    config: DraftQualityConfig | Mapping[str, Any] | None = None,
    rules_version: str = DRAFT_QUALITY_RULES_VERSION,
) -> DeterministicDraftQualityResult:
    """Run explainable, deterministic checks over the spoken script.

    This function deliberately reports observable patterns. It does not claim
    to detect whether text was written by AI.
    """

    parsed_config = DraftQualityConfig.model_validate(config or {})
    if not parsed_config.enabled:
        raise ValueError("draft quality analysis is disabled")
    if rules_version not in {
        LEGACY_DRAFT_QUALITY_RULES_VERSION,
        PREVIOUS_DRAFT_QUALITY_RULES_VERSION,
        DRAFT_QUALITY_RULES_VERSION,
    }:
        raise ValueError("unsupported Draft quality rules version")
    use_legacy_rules = rules_version == LEGACY_DRAFT_QUALITY_RULES_VERSION
    use_editorial_instruction_rule = rules_version == DRAFT_QUALITY_RULES_VERSION
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
    if use_legacy_rules:
        must_include_status = "warning" if missing_must_include else "pass"
        if missing_must_include:
            penalties += min(16, len(missing_must_include) * 4)
        must_include_threshold = "all literal items present"
    else:
        # `must_include` describes content requirements, not necessarily exact
        # wording. A literal miss is reproducible evidence but cannot prove that
        # a paraphrased idea is absent, so it is informational and model-reviewed.
        must_include_status = "info" if missing_must_include else "pass"
        must_include_threshold = (
            "literal substring observation only; semantic coverage is model-reviewed"
        )
    findings.append(
        _finding(
            "brief.must_include",
            must_include_status,
            location="creative_brief.must_include",
            exact_quote=missing_must_include[0] if missing_must_include else "",
            observed="、".join(missing_must_include) if missing_must_include else 0,
            threshold=must_include_threshold,
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
            threshold=(
                0
                if use_legacy_rules
                else "0 literal substring hits; abstract or paraphrased avoidance is model-reviewed"
            ),
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
    if use_legacy_rules and template_status == "warning":
        penalties += min(12, (template_count - _TEMPLATE_WARNING_COUNT) * 3)
    elif not use_legacy_rules and template_status == "warning":
        # Kept as a legacy metric for display. The versioned Chinese categories
        # below own the score impact and prevent double-penalizing one phrase.
        template_status = "info"
    findings.append(
        _finding(
            "style.template_phrases",
            template_status,
            location="podcast_script",
            exact_quote=template_example,
            observed=template_count,
            threshold=(
                f"<= {_TEMPLATE_WARNING_COUNT}"
                if use_legacy_rules
                else (
                    "legacy observation only; score impact is owned by versioned "
                    "style.zh categories"
                )
            ),
        )
    )

    not_but_matches = list(_NOT_BUT_PATTERN.finditer(script_text))
    not_but_count = len(not_but_matches)
    not_but_status = "warning" if not_but_count > _NOT_BUT_WARNING_COUNT else "pass"
    if use_legacy_rules and not_but_status == "warning":
        penalties += min(9, (not_but_count - _NOT_BUT_WARNING_COUNT) * 3)
    elif not use_legacy_rules and not_but_status == "warning":
        # Also retained for display. The broader parallel-contrast category
        # owns the current score impact.
        not_but_status = "info"
    findings.append(
        _finding(
            "style.not_but_pattern",
            not_but_status,
            location="podcast_script",
            exact_quote=not_but_matches[0].group(0) if not_but_matches else "",
            observed=not_but_count,
            threshold=(
                f"<= {_NOT_BUT_WARNING_COUNT}"
                if use_legacy_rules
                else (
                    "legacy observation only; score impact is owned by style.zh.parallel_contrast"
                )
            ),
        )
    )

    editorial_instruction_count = 0
    if use_editorial_instruction_rule:
        editorial_instruction_matches = list(_EDITORIAL_INSTRUCTION_PATTERN.finditer(script_text))
        editorial_instruction_count = len(editorial_instruction_matches)
        editorial_instruction_status = "warning" if editorial_instruction_count else "pass"
        if editorial_instruction_status == "warning":
            penalties += min(12, 6 + editorial_instruction_count * 2)
        findings.append(
            _finding(
                "style.editorial_instruction_leakage",
                editorial_instruction_status,
                location="podcast_script",
                exact_quote=(
                    editorial_instruction_matches[0].group(0)
                    if editorial_instruction_matches
                    else ""
                ),
                observed=editorial_instruction_count,
                threshold="0 meta-editing instructions in spoken text",
            )
        )

    style_pattern_counts = ChineseStylePatternCounts()
    sentence_lengths: list[int] = []
    paragraph_lengths: list[int] = []
    sentence_length_cv: float | None = None
    paragraph_length_cv: float | None = None
    if not use_legacy_rules:
        style_patterns = dict(_CHINESE_STYLE_PATTERNS)
        if rules_version == PREVIOUS_DRAFT_QUALITY_RULES_VERSION:
            style_patterns["enumeration"] = _PREVIOUS_ENUMERATION_PATTERN
        style_observations = _chinese_style_observations(
            paragraphs,
            patterns=style_patterns,
        )
        for category, (count, location, exact_quote) in style_observations.items():
            warning_minimum = _CHINESE_STYLE_WARNING_MINIMUMS[category]
            status = "warning" if count >= warning_minimum else "pass"
            if status == "warning":
                penalties += min(6, count - warning_minimum + 2)
            findings.append(
                _finding(
                    f"style.zh.{category}",
                    status,
                    location=location,
                    exact_quote=exact_quote,
                    observed=count,
                    threshold=f"< {warning_minimum} occurrences in spoken text",
                )
            )

        sentence_lengths = _spoken_sentence_lengths(paragraph_texts)
        paragraph_lengths = [
            len(_non_whitespace(text)) for text in paragraph_texts if _non_whitespace(text)
        ]
        sentence_length_cv = _coefficient_of_variation(
            sentence_lengths,
            minimum_sample=_MIN_SENTENCE_CV_SAMPLE,
        )
        paragraph_length_cv = _coefficient_of_variation(
            paragraph_lengths,
            minimum_sample=_MIN_PARAGRAPH_CV_SAMPLE,
        )
        if sentence_length_cv is not None:
            sentence_cv_status = (
                "warning" if sentence_length_cv < _SENTENCE_LENGTH_CV_WARNING_BELOW else "pass"
            )
            if sentence_cv_status == "warning":
                penalties += 4
            findings.append(
                _finding(
                    "style.sentence_length_cv",
                    sentence_cv_status,
                    location="podcast_script",
                    observed=round(sentence_length_cv, 4),
                    threshold=f">= {_SENTENCE_LENGTH_CV_WARNING_BELOW}",
                )
            )
        if paragraph_length_cv is not None:
            paragraph_cv_status = (
                "warning" if paragraph_length_cv < _PARAGRAPH_LENGTH_CV_WARNING_BELOW else "pass"
            )
            if paragraph_cv_status == "warning":
                penalties += 4
            findings.append(
                _finding(
                    "style.paragraph_length_cv",
                    paragraph_cv_status,
                    location="podcast_script",
                    observed=round(paragraph_length_cv, 4),
                    threshold=f">= {_PARAGRAPH_LENGTH_CV_WARNING_BELOW}",
                )
            )

        style_pattern_counts = ChineseStylePatternCounts.model_validate(
            {category: observation[0] for category, observation in style_observations.items()}
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
        editorial_instruction_phrase_count=editorial_instruction_count,
        rules_version=rules_version,
        chinese_style_heuristic_version=(
            None
            if use_legacy_rules
            else PREVIOUS_CHINESE_STYLE_HEURISTIC_VERSION
            if rules_version == PREVIOUS_DRAFT_QUALITY_RULES_VERSION
            else CHINESE_STYLE_HEURISTIC_VERSION
        ),
        chinese_style_pattern_counts=style_pattern_counts,
        spoken_sentence_count=len(sentence_lengths),
        spoken_nonempty_paragraph_count=len(paragraph_lengths),
        sentence_length_cv=(None if sentence_length_cv is None else round(sentence_length_cv, 4)),
        paragraph_length_cv=(
            None if paragraph_length_cv is None else round(paragraph_length_cv, 4)
        ),
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
    deepseek_v4_tiers = {"deepseek-v4-flash", "deepseek-v4-pro"}
    if (
        editor_provider == reviewer_provider == "deepseek"
        and {editor_model, reviewer_model} <= deepseek_v4_tiers
    ):
        return "cross_tier_same_family"
    return "different_model"


def _experimental_model_score(
    review: ModelSelfReviewOutput,
    *,
    scoring_formula_version: str = DRAFT_QUALITY_FORMULA_VERSION,
    writing_style_context_status: WritingStyleContextStatus = "not_provided",
) -> float | None:
    # A partial set of model dimensions is still useful as cards, but folding
    # it into one number would hide what the model admitted it could not judge.
    return calculate_model_review_score(
        review,
        scoring_formula_version=scoring_formula_version,
        writing_style_context_status=writing_style_context_status,
    )


def build_deterministic_quality_facts(
    deterministic: DeterministicDraftQualityResult,
) -> DeterministicQualityFacts:
    """Project the persisted deterministic Artifact into a bounded Reviewer bundle."""

    rules_version = deterministic.metrics.rules_version
    if rules_version not in {
        PREVIOUS_DRAFT_QUALITY_RULES_VERSION,
        DRAFT_QUALITY_RULES_VERSION,
    }:
        raise ValueError("Reviewer facts require a supported deterministic ruleset")
    if deterministic.metrics.chinese_style_heuristic_version is None:
        raise ValueError("Reviewer facts require the Chinese style heuristic")
    duration_finding = next(
        (
            finding
            for finding in deterministic.findings
            if finding.code == "draft.empty" or finding.code.startswith("duration.")
        ),
        None,
    )
    if duration_finding is None:
        raise ValueError("deterministic quality result has no duration finding")
    metrics = deterministic.metrics
    target = metrics.target_duration_minutes
    coverage_ratio = metrics.estimated_duration_minutes / target if target else 0.0
    return DeterministicQualityFacts(
        facts_version=(
            LEGACY_DETERMINISTIC_QUALITY_FACTS_VERSION
            if rules_version == PREVIOUS_DRAFT_QUALITY_RULES_VERSION
            else DETERMINISTIC_QUALITY_FACTS_VERSION
        ),
        rules_version=rules_version,
        chinese_style_heuristic_version=(deterministic.metrics.chinese_style_heuristic_version),
        quality_profile=deterministic.profile,
        deterministic_score=deterministic.deterministic_score,
        target_duration_minutes=target,
        script_character_count=metrics.script_character_count,
        estimated_duration_minutes=metrics.estimated_duration_minutes,
        duration_coverage_ratio=round(coverage_ratio, 4),
        duration_status=duration_finding.status,
        duration_finding_code=duration_finding.code,
        paragraph_citation_coverage=metrics.paragraph_citation_coverage,
        blocker_count=sum(finding.status == "blocker" for finding in deterministic.findings),
        warning_count=sum(finding.status == "warning" for finding in deterministic.findings),
        chinese_style_pattern_counts=metrics.chinese_style_pattern_counts,
        filler_phrase_count=metrics.filler_phrase_count,
        template_phrase_count=metrics.template_phrase_count,
        not_but_pattern_count=metrics.not_but_pattern_count,
        editorial_instruction_phrase_count=(metrics.editorial_instruction_phrase_count),
    )


def _code_owned_score_cap(
    deterministic: DeterministicDraftQualityResult,
) -> tuple[int, list[QualityScoreCapReason]]:
    """Return the strictest applicable non-compensatory cap and all reasons."""

    reasons: list[QualityScoreCapReason] = []
    metrics = deterministic.metrics
    duration_coverage = (
        metrics.estimated_duration_minutes / metrics.target_duration_minutes
        if metrics.target_duration_minutes
        else 0.0
    )
    if deterministic.has_blocker:
        reasons.append(
            QualityScoreCapReason(
                code="deterministic_blocker_cap",
                cap=39,
                explanation="确定性规则存在 blocker，模型高分不能补偿硬性问题。",
            )
        )
    if duration_coverage < 0.60:
        reasons.append(
            QualityScoreCapReason(
                code="duration_coverage_below_60_percent_cap",
                cap=59,
                explanation=(
                    f"估算时长只达到目标的 {duration_coverage:.1%}，"
                    "综合分不得掩盖明显的素材或篇幅缺口。"
                ),
            )
        )
    if deterministic.has_warning:
        reasons.append(
            QualityScoreCapReason(
                code="deterministic_warning_cap",
                cap=79,
                explanation="确定性规则仍有 warning，候选稿不能显示为 80 分以上。",
            )
        )
    return min((reason.cap for reason in reasons), default=100), reasons


def _model_review_conflicts(
    *,
    deterministic: DeterministicDraftQualityResult,
    review: ModelSelfReviewOutput | None,
) -> list[ModelReviewConflict]:
    """Record material disagreements while preserving the raw model cards."""

    if review is None:
        return []
    metrics = deterministic.metrics
    duration_coverage = (
        metrics.estimated_duration_minutes / metrics.target_duration_minutes
        if metrics.target_duration_minutes
        else 0.0
    )
    duration_finding = next(
        (
            finding
            for finding in deterministic.findings
            if finding.code == "draft.empty" or finding.code.startswith("duration.")
        ),
        None,
    )
    if duration_finding is None:
        return []
    if duration_finding.status == "blocker" or duration_coverage < 0.60:
        maximum_allowed_score = 2
    elif duration_finding.status == "warning":
        maximum_allowed_score = 3
    else:
        return []
    brief_card = next(
        (card for card in review.dimensions if card.dimension == "brief_adherence"),
        None,
    )
    if (
        brief_card is None
        or not brief_card.assessable
        or brief_card.score is None
        or brief_card.score <= maximum_allowed_score
    ):
        return []
    return [
        ModelReviewConflict(
            code="duration_vs_brief_adherence_score",
            dimension="brief_adherence",
            model_score=brief_card.score,
            deterministic_finding_codes=[duration_finding.code],
            explanation=(
                f"Reviewer 给创作要求匹配 {brief_card.score}/5，"
                f"但代码估算时长仅达到目标的 {duration_coverage:.1%}。"
                f"当前事实最多支持 {maximum_allowed_score}/5。"
                "原始模型卡保留不变，最终综合分由代码上限校准。"
            ),
        )
    ]


def build_draft_quality_report(
    *,
    deterministic: DeterministicDraftQualityResult,
    model_self_review: ModelSelfReviewOutput | Mapping[str, Any] | None,
    editor_provider: str | None = None,
    editor_model: str | None = None,
    reviewer_provider: str | None = None,
    reviewer_model: str | None = None,
    unavailable_reason: str | None = None,
    scoring_formula_version: str = DRAFT_QUALITY_FORMULA_VERSION,
    writing_style_context_status: WritingStyleContextStatus = "not_provided",
) -> DraftQualityReport:
    """Combine reviews without allowing a model to erase objective blockers."""

    parsed_review = (
        None
        if model_self_review is None
        else ModelSelfReviewOutput.model_validate(model_self_review)
    )
    model_score = (
        None
        if parsed_review is None
        else _experimental_model_score(
            parsed_review,
            scoring_formula_version=scoring_formula_version,
            writing_style_context_status=writing_style_context_status,
        )
    )
    uncapped_overall_score = (
        None
        if model_score is None
        else round(0.6 * deterministic.deterministic_score + 0.4 * model_score, 2)
    )
    if scoring_formula_version == LEGACY_DRAFT_QUALITY_FORMULA_VERSION:
        score_cap: int | None = None
        cap_reasons: list[QualityScoreCapReason] = []
        overall_score = uncapped_overall_score
        conflicts: list[ModelReviewConflict] = []
    elif scoring_formula_version in {
        DRAFT_QUALITY_FORMULA_VERSION,
        STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION,
    }:
        score_cap, cap_reasons = _code_owned_score_cap(deterministic)
        overall_score = (
            None
            if uncapped_overall_score is None
            else min(uncapped_overall_score, float(score_cap))
        )
        conflicts = _model_review_conflicts(
            deterministic=deterministic,
            review=parsed_review,
        )
    else:
        raise ValueError("unsupported Draft quality scoring formula version")

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
        scoring_formula_version=scoring_formula_version,
        writing_style_context_status=writing_style_context_status,
        experimental_model_score=model_score,
        experimental_uncapped_overall_score=(
            None
            if scoring_formula_version == LEGACY_DRAFT_QUALITY_FORMULA_VERSION
            else uncapped_overall_score
        ),
        code_owned_score_cap=score_cap,
        score_cap_reasons=cap_reasons,
        model_review_conflicts=conflicts,
        experimental_overall_score=overall_score,
        decision=decision,
    )
