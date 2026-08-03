from __future__ import annotations

from copy import deepcopy

import pytest

from epiphany.draft_quality import (
    analyze_podcast_draft,
    build_deterministic_quality_facts,
    build_draft_quality_report,
)
from epiphany.draft_quality_markdown import render_draft_quality_markdown
from epiphany.draft_quality_schemas import (
    CHINESE_STYLE_HEURISTIC_VERSION,
    DRAFT_QUALITY_RULES_VERSION,
    LEGACY_DRAFT_QUALITY_FORMULA_VERSION,
    LEGACY_DRAFT_QUALITY_RULES_VERSION,
    PREVIOUS_CHINESE_STYLE_HEURISTIC_VERSION,
    PREVIOUS_DRAFT_QUALITY_RULES_VERSION,
    REVIEW_DIMENSIONS,
    STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION,
    DeterministicDraftMetrics,
    DeterministicQualityFacts,
    DraftQualityReport,
    InvalidModelReviewEvidence,
    InvalidModelReviewSourceReference,
    ModelSelfReviewOutput,
    ModelSelfReviewSchemaError,
    validate_model_self_review_output,
)
from epiphany.editor_schemas import PodcastDraftOutput
from epiphany.quality_contract_schemas import CreativeBrief, DraftQualityConfig


def _reference(index: int) -> dict[str, str]:
    return {
        "source_id": f"src_{index}",
        "source_segment_id": f"seg_{index}",
    }


def _grounded(text: str, index: int = 0) -> dict[str, object]:
    return {"text": text, "source_refs": [_reference(index)]}


def _unique_chinese(start: int, length: int) -> str:
    return "".join(chr(0x4E00 + start + index) for index in range(length))


def _good_draft() -> PodcastDraftOutput:
    paragraphs = [
        _unique_chinese(0, 550) + "旧录音",
        _unique_chinese(600, 550),
        _unique_chinese(1_200, 550),
        _unique_chinese(1_800, 550) + "重新开始",
    ]
    return PodcastDraftOutput.model_validate(
        {
            "title": "五年后重新打开播客",
            "podcast_script": {
                "opening": _grounded(_unique_chinese(2_400, 300), 0),
                "sections": [
                    {
                        "title": "旧声音",
                        "source_refs": [_reference(0), _reference(1)],
                        "paragraphs": [
                            _grounded(paragraphs[0], 0),
                            _grounded(paragraphs[1], 1),
                        ],
                    },
                    {
                        "title": "新的开始",
                        "source_refs": [_reference(2), _reference(3)],
                        "paragraphs": [
                            _grounded(paragraphs[2], 2),
                            _grounded(paragraphs[3], 3),
                        ],
                    },
                ],
                "closing": _grounded(_unique_chinese(2_750, 280), 3),
            },
            "show_notes": {
                "summary": _grounded("五年后重听旧录音，并决定重新开始记录。", 0),
                "key_points": [
                    _grounded("声音保存语气、呼吸与停顿。", 1),
                    _grounded("先完成一版能听的内容。", 3),
                ],
            },
        }
    )


def _draft_with_script_character_count(character_count: int) -> PodcastDraftOutput:
    draft = _good_draft().model_dump(mode="python")
    blocks = [
        draft["podcast_script"]["opening"],
        *[
            paragraph
            for section in draft["podcast_script"]["sections"]
            for paragraph in section["paragraphs"]
        ],
        draft["podcast_script"]["closing"],
    ]
    per_block, remainder = divmod(character_count, len(blocks))
    for index, block in enumerate(blocks):
        block["text"] = "声" * (per_block + (1 if index < remainder else 0))
    return PodcastDraftOutput.model_validate(draft)


def _brief() -> CreativeBrief:
    return CreativeBrief(
        target_duration_minutes=10,
        speaking_rate_chars_per_minute=280,
        must_include=["旧录音", "重新开始"],
        avoid_patterns=["在这个快节奏的时代"],
    )


def _task_input(draft: PodcastDraftOutput | None = None) -> dict[str, object]:
    selected = draft or _good_draft()
    references = [_reference(index) for index in range(4)]
    deterministic = analyze_podcast_draft(
        draft=selected,
        creative_brief=_brief(),
    )
    return {
        "task_kind": "review_podcast_draft",
        "draft_artifact_id": "art_draft",
        "deterministic_metrics_artifact_id": "art_metrics",
        "deterministic_quality_facts": build_deterministic_quality_facts(deterministic).model_dump(
            mode="json"
        ),
        "creative_brief": _brief().model_dump(mode="json"),
        "quality_config": DraftQualityConfig().model_dump(mode="json"),
        "podcast_draft": selected.model_dump(mode="json"),
        "allowed_source_refs": references,
        "referenced_source_segments": [
            {**reference, "text": f"测试来源片段 {index}"}
            for index, reference in enumerate(references)
        ],
    }


def _model_review(
    *,
    score: int = 4,
    quote: str | None = None,
    source_ref: dict[str, str] | None = None,
) -> dict[str, object]:
    draft = _good_draft()
    evidence_quote = quote or draft.podcast_script.opening.text[:12]
    dimensions: list[dict[str, object]] = []
    for dimension in REVIEW_DIMENSIONS:
        evidence: dict[str, object] = {
            "location": "podcast_script.opening",
            "exact_quote": evidence_quote,
            "source_refs": [],
        }
        if dimension == "source_faithfulness":
            evidence["source_refs"] = [source_ref or _reference(0)]
        dimensions.append(
            {
                "dimension": dimension,
                "assessable": True,
                "score": score,
                "assessment": f"{dimension} 有明确的逐字证据。",
                "limitation": None,
                "evidence": [evidence],
            }
        )
    return {
        "review_kind": "model_self_review",
        "advisory": True,
        "dimensions": dimensions,
    }


def _style_aware_model_review(
    *,
    base_score: int,
    personal_style_score: int,
) -> dict[str, object]:
    review = _model_review(score=base_score)
    review["dimensions"].append(
        {
            "dimension": "personal_style_match",
            "assessable": True,
            "score": personal_style_score,
            "assessment": "初稿和个人样本都使用具体画面推进反思，但句子节奏仍有差异。",
            "limitation": None,
            "evidence": [
                {
                    "location": "podcast_script.opening",
                    "exact_quote": _good_draft().podcast_script.opening.text[:12],
                    "source_refs": [],
                }
            ],
            "style_sample_evidence": [
                {
                    "location": "writing_style_segments[0]",
                    "exact_quote": "我通常会先写下一个很小的画面。",
                    "source_ref": {
                        "source_id": "src_style",
                        "source_segment_id": "seg_style",
                    },
                }
            ],
        }
    )
    return review


def test_config_is_versioned_enabled_and_forbids_unknown_fields() -> None:
    assert DraftQualityConfig().model_dump() == {
        "enabled": True,
        "profile": "podcast_draft_v1",
    }
    with pytest.raises(ValueError):
        DraftQualityConfig.model_validate({"enabled": False, "unknown": True})


def test_good_draft_reports_explainable_metrics_without_ai_probability() -> None:
    result = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    )

    assert 8.5 <= result.metrics.estimated_duration_minutes <= 11.5
    assert result.metrics.paragraph_citation_coverage == 1
    assert result.metrics.unique_source_count == 4
    assert result.metrics.unique_segment_count == 4
    assert result.metrics.exact_duplicate_paragraph_count == 0
    assert result.metrics.must_include_missing_count == 0
    assert result.metrics.avoid_pattern_hit_count == 0
    assert not result.has_blocker
    assert all(
        {"code", "status", "location", "exact_quote", "observed", "threshold"}
        == set(finding.model_dump())
        for finding in result.findings
    )
    assert "probability" not in result.model_dump_json().lower()


@pytest.mark.parametrize("target_minutes", [10, 15, 30])
def test_duration_metric_honors_each_supported_target(target_minutes: int) -> None:
    expected_characters = target_minutes * 280
    result = analyze_podcast_draft(
        draft=_draft_with_script_character_count(expected_characters),
        creative_brief=CreativeBrief(
            target_duration_minutes=target_minutes,
            speaking_rate_chars_per_minute=280,
        ),
    )

    assert result.metrics.target_duration_minutes == target_minutes
    assert result.metrics.script_character_count == expected_characters
    assert result.metrics.estimated_duration_minutes == target_minutes
    assert result.findings[0].code == "duration.within_target_range"
    assert result.findings[0].status == "pass"


def test_duration_counts_only_spoken_text_not_metadata_references_or_rendered_markdown() -> None:
    baseline_content = _good_draft().model_dump(mode="python")
    baseline = analyze_podcast_draft(
        draft=baseline_content,
        creative_brief=_brief(),
    )
    metadata_heavy = deepcopy(baseline_content)
    metadata_heavy["title"] = "首先，值得注意的是。" * 500
    spoken_blocks = [
        metadata_heavy["podcast_script"]["opening"],
        *[
            paragraph
            for section in metadata_heavy["podcast_script"]["sections"]
            for paragraph in section["paragraphs"]
        ],
        metadata_heavy["podcast_script"]["closing"],
    ]
    for block_index, block in enumerate(spoken_blocks):
        block["source_refs"] = [
            {
                "source_id": f"src_{block_index}_" + ("让我们一起" * 500),
                "source_segment_id": f"seg_{block_index}_" + ("我突然意识到" * 500),
            }
        ]
    for section in metadata_heavy["podcast_script"]["sections"]:
        section["title"] = "不是标题而是元数据。" * 300
        for reference in section["source_refs"]:
            reference["source_id"] = "src_" + ("来源索引" * 500)
    metadata_heavy["show_notes"]["summary"]["text"] = "让我们一起。" * 1_000
    for point in metadata_heavy["show_notes"]["key_points"]:
        point["text"] = "我突然意识到。" * 1_000
    metadata_heavy["rendered_markdown"] = (
        "# 标题\n\n来源：[S1]\n\n## 来源索引\n- [S1] 很长的渲染引用\n" * 1_000
    )

    result = analyze_podcast_draft(
        draft=metadata_heavy,
        creative_brief=_brief(),
    )

    assert result.metrics.script_character_count == baseline.metrics.script_character_count
    assert result.metrics.estimated_duration_minutes == baseline.metrics.estimated_duration_minutes
    assert result.metrics.chinese_style_pattern_counts.model_dump() == {
        "parallel_contrast": 0,
        "escalation": 0,
        "enumeration": 0,
        "generic_transition": 0,
        "generic_epiphany": 0,
        "generic_coda": 0,
        "over_polite": 0,
    }


def test_short_or_uncited_draft_is_an_objective_blocker() -> None:
    content = _good_draft().model_dump(mode="python")
    content["podcast_script"]["opening"]["text"] = "太短了。"
    for section in content["podcast_script"]["sections"]:
        for paragraph in section["paragraphs"]:
            paragraph["text"] = "仍然太短。"
    content["podcast_script"]["closing"]["text"] = "结束。"
    content["podcast_script"]["sections"][0]["paragraphs"][0]["source_refs"] = []

    result = analyze_podcast_draft(draft=content, creative_brief=_brief())

    blockers = {finding.code for finding in result.findings if finding.status == "blocker"}
    assert "duration.severe_deviation" in blockers
    assert "citations.paragraph_coverage" in blockers
    assert result.deterministic_score < 50


def test_duplicate_windows_brief_literals_and_canonical_style_rules_are_warnings() -> None:
    content = _good_draft().model_dump(mode="python")
    repeated = (
        "在这个快节奏的时代，值得注意的是，这不是结论而是开场。总而言之，让我们一起继续。"
    ) * 15
    first = content["podcast_script"]["sections"][0]["paragraphs"][0]
    second = content["podcast_script"]["sections"][0]["paragraphs"][1]
    first["text"] = repeated
    second["text"] = " \n".join(repeated)

    result = analyze_podcast_draft(draft=content, creative_brief=_brief())
    warnings = {finding.code for finding in result.findings if finding.status == "warning"}

    assert result.metrics.exact_duplicate_paragraph_count == 1
    assert result.metrics.repeated_eight_character_window_ratio > 0.12
    assert result.metrics.avoid_pattern_hit_count > 0
    assert result.metrics.template_phrase_count > 2
    assert result.metrics.not_but_pattern_count > 2
    assert "repetition.exact_normalized_paragraphs" in warnings
    assert "repetition.eight_character_windows" in warnings
    assert "brief.avoid_patterns" in warnings
    assert "style.zh.generic_transition" in warnings
    assert "style.zh.generic_coda" in warnings
    assert "style.zh.parallel_contrast" in warnings

    findings = {finding.code: finding for finding in result.findings}
    assert "literal substring hits" in str(findings["brief.avoid_patterns"].threshold)
    assert findings["style.template_phrases"].status == "info"
    assert findings["style.not_but_pattern"].status == "info"


def test_missing_must_include_literal_is_informational_not_semantic_failure() -> None:
    content = _good_draft()
    baseline = analyze_podcast_draft(
        draft=content,
        creative_brief=CreativeBrief(
            target_duration_minutes=10,
            speaking_rate_chars_per_minute=280,
        ),
    )
    observed = analyze_podcast_draft(
        draft=content,
        creative_brief=CreativeBrief(
            target_duration_minutes=10,
            speaking_rate_chars_per_minute=280,
            must_include=["允许自己在没有答案时继续表达"],
        ),
    )

    finding = next(finding for finding in observed.findings if finding.code == "brief.must_include")
    assert observed.metrics.must_include_missing_count == 1
    assert finding.status == "info"
    assert "semantic coverage is model-reviewed" in str(finding.threshold)
    assert observed.deterministic_score == baseline.deterministic_score
    assert observed.has_warning == baseline.has_warning


def test_versioned_chinese_style_categories_report_counts_quotes_and_locations() -> None:
    content = _good_draft().model_dump(mode="python")
    location = "podcast_script.sections[0].paragraphs[0]"
    content["podcast_script"]["sections"][0]["paragraphs"][0]["text"] += (
        "不是为了证明而是为了记录。" * 3
        + "不仅要写还要说。" * 3
        + "首先，回忆。其次，理解。最后，继续。"
        + "值得注意的是，先停一下。" * 3
        + "我突然意识到，声音还在。" * 3
        + "让我们一起继续记录。" * 3
        + "非常荣幸和你分享。" * 2
    )

    result = analyze_podcast_draft(draft=content, creative_brief=_brief())
    counts = result.metrics.chinese_style_pattern_counts

    assert result.metrics.chinese_style_heuristic_version == CHINESE_STYLE_HEURISTIC_VERSION
    assert result.metrics.rules_version == DRAFT_QUALITY_RULES_VERSION
    assert counts.parallel_contrast == 3
    assert counts.escalation == 3
    assert counts.enumeration == 3
    assert counts.generic_transition == 3
    assert counts.generic_epiphany == 3
    assert counts.generic_coda == 3
    assert counts.over_polite == 2
    categorized_findings = {
        finding.code: finding for finding in result.findings if finding.code.startswith("style.zh.")
    }
    assert len(categorized_findings) == 7
    assert all(finding.status == "warning" for finding in categorized_findings.values())
    assert all(finding.location == location for finding in categorized_findings.values())
    assert all(finding.exact_quote for finding in categorized_findings.values())


def test_enumeration_does_not_count_ordinary_uses_of_last() -> None:
    content = _good_draft().model_dump(mode="python")
    content["podcast_script"]["opening"]["text"] = (
        "我拉着最后一个空行李箱下楼，最后用报纸包好那只碗，最后取消了订单。"
    )

    result = analyze_podcast_draft(draft=content, creative_brief=_brief())
    finding = next(item for item in result.findings if item.code == "style.zh.enumeration")

    assert result.metrics.chinese_style_pattern_counts.enumeration == 0
    assert finding.status == "pass"


def test_previous_chinese_rules_remain_replayable_after_precision_upgrade() -> None:
    content = _good_draft().model_dump(mode="python")
    content["podcast_script"]["opening"]["text"] = (
        "我拉着最后一个空行李箱下楼，最后用报纸包好那只碗，最后取消了订单。"
    )

    previous = analyze_podcast_draft(
        draft=content,
        creative_brief=_brief(),
        rules_version=PREVIOUS_DRAFT_QUALITY_RULES_VERSION,
    )
    previous_codes = {finding.code for finding in previous.findings}
    previous_facts = build_deterministic_quality_facts(previous)

    assert previous.metrics.rules_version == PREVIOUS_DRAFT_QUALITY_RULES_VERSION
    assert (
        previous.metrics.chinese_style_heuristic_version == PREVIOUS_CHINESE_STYLE_HEURISTIC_VERSION
    )
    assert previous.metrics.chinese_style_pattern_counts.enumeration == 3
    assert "style.editorial_instruction_leakage" not in previous_codes
    assert previous_facts.facts_version == "deterministic_quality_facts_v1"
    assert previous_facts.rules_version == PREVIOUS_DRAFT_QUALITY_RULES_VERSION
    pre_versioned_facts = previous_facts.model_dump(mode="python")
    pre_versioned_facts.pop("facts_version")
    assert (
        DeterministicQualityFacts.model_validate(pre_versioned_facts).facts_version
        == "deterministic_quality_facts_v1"
    )


def test_editorial_instructions_in_spoken_text_are_reported_for_review() -> None:
    content = _good_draft().model_dump(mode="python")
    content["podcast_script"]["sections"][0]["paragraphs"][0]["text"] += (
        "还有一点需要在正文里解释。这句话有点抽象，如果要用，前面一定要先放具体场景。"
    )

    result = analyze_podcast_draft(draft=content, creative_brief=_brief())
    finding = next(
        item for item in result.findings if item.code == "style.editorial_instruction_leakage"
    )
    facts = build_deterministic_quality_facts(result)

    assert result.metrics.editorial_instruction_phrase_count >= 1
    assert finding.status == "warning"
    assert finding.exact_quote
    assert facts.editorial_instruction_phrase_count == (
        result.metrics.editorial_instruction_phrase_count
    )


def test_sentence_and_paragraph_cv_warn_only_with_enough_samples() -> None:
    uniform = _good_draft().model_dump(mode="python")
    uniform["podcast_script"]["opening"]["text"] = "甲乙丙丁。"
    uniform["podcast_script"]["closing"]["text"] = "戊己庚辛。"
    for section in uniform["podcast_script"]["sections"]:
        for paragraph in section["paragraphs"]:
            paragraph["text"] = "壬癸子丑。"

    measured = analyze_podcast_draft(draft=uniform, creative_brief=_brief())
    measured_findings = {finding.code: finding for finding in measured.findings}

    assert measured.metrics.spoken_sentence_count == 6
    assert measured.metrics.spoken_nonempty_paragraph_count == 6
    assert measured.metrics.sentence_length_cv == 0
    assert measured.metrics.paragraph_length_cv == 0
    assert measured_findings["style.sentence_length_cv"].status == "warning"
    assert measured_findings["style.paragraph_length_cv"].status == "warning"

    insufficient = deepcopy(uniform)
    insufficient["podcast_script"]["sections"] = []
    insufficient["podcast_script"]["closing"]["text"] = ""
    not_measured = analyze_podcast_draft(draft=insufficient, creative_brief=_brief())
    not_measured_codes = {finding.code for finding in not_measured.findings}

    assert not_measured.metrics.spoken_sentence_count == 1
    assert not_measured.metrics.spoken_nonempty_paragraph_count == 1
    assert not_measured.metrics.sentence_length_cv is None
    assert not_measured.metrics.paragraph_length_cv is None
    assert "style.sentence_length_cv" not in not_measured_codes
    assert "style.paragraph_length_cv" not in not_measured_codes


def test_legacy_deterministic_metrics_gain_safe_defaults_for_new_style_fields() -> None:
    current = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    ).metrics.model_dump(mode="python")
    for field in (
        "rules_version",
        "chinese_style_heuristic_version",
        "chinese_style_pattern_counts",
        "spoken_sentence_count",
        "spoken_nonempty_paragraph_count",
        "sentence_length_cv",
        "paragraph_length_cv",
    ):
        current.pop(field)

    restored = DeterministicDraftMetrics.model_validate(current)

    assert restored.rules_version == LEGACY_DRAFT_QUALITY_RULES_VERSION
    assert restored.chinese_style_heuristic_version is None
    assert restored.chinese_style_pattern_counts.model_dump() == {
        "parallel_contrast": 0,
        "escalation": 0,
        "enumeration": 0,
        "generic_transition": 0,
        "generic_epiphany": 0,
        "generic_coda": 0,
        "over_polite": 0,
    }
    assert restored.sentence_length_cv is None
    assert restored.paragraph_length_cv is None


def test_pre_release_chinese_metrics_without_rules_field_infer_current_rules() -> None:
    current = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    ).metrics.model_dump(mode="python")
    current.pop("rules_version")

    restored = DeterministicDraftMetrics.model_validate(current)

    assert restored.rules_version == DRAFT_QUALITY_RULES_VERSION
    assert restored.chinese_style_heuristic_version == CHINESE_STYLE_HEURISTIC_VERSION


def test_pre_release_v2_metrics_without_rules_field_infer_previous_rules() -> None:
    previous = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
        rules_version=PREVIOUS_DRAFT_QUALITY_RULES_VERSION,
    ).metrics.model_dump(mode="python")
    previous.pop("rules_version")

    restored = DeterministicDraftMetrics.model_validate(previous)

    assert restored.rules_version == PREVIOUS_DRAFT_QUALITY_RULES_VERSION
    assert restored.chinese_style_heuristic_version == PREVIOUS_CHINESE_STYLE_HEURISTIC_VERSION


def test_legacy_rules_preserve_m3_4_literal_and_template_penalties() -> None:
    content = _good_draft().model_dump(mode="python")
    content["podcast_script"]["opening"]["text"] += (
        "总而言之，总而言之，总而言之。不是为了展示而是为了说明。" * 3
    )
    brief = CreativeBrief(
        target_duration_minutes=10,
        must_include=["必须逐字出现但实际没有"],
    )

    legacy = analyze_podcast_draft(
        draft=content,
        creative_brief=brief,
        rules_version=LEGACY_DRAFT_QUALITY_RULES_VERSION,
    )
    current = analyze_podcast_draft(
        draft=content,
        creative_brief=brief,
        rules_version=DRAFT_QUALITY_RULES_VERSION,
    )
    legacy_findings = {finding.code: finding for finding in legacy.findings}
    current_findings = {finding.code: finding for finding in current.findings}

    assert legacy.metrics.rules_version == LEGACY_DRAFT_QUALITY_RULES_VERSION
    assert legacy.metrics.chinese_style_heuristic_version is None
    assert legacy_findings["brief.must_include"].status == "warning"
    assert legacy_findings["style.template_phrases"].status == "warning"
    assert legacy_findings["style.not_but_pattern"].status == "warning"
    assert all(not code.startswith("style.zh.") for code in legacy_findings)
    assert current.metrics.rules_version == DRAFT_QUALITY_RULES_VERSION
    assert current_findings["brief.must_include"].status == "info"
    assert current_findings["style.template_phrases"].status == "info"
    assert current_findings["style.not_but_pattern"].status == "info"
    assert any(code.startswith("style.zh.") for code in current_findings)


def test_disabled_analysis_is_an_explicit_opt_out_not_a_pass() -> None:
    with pytest.raises(ValueError, match="disabled"):
        analyze_podcast_draft(
            draft=_good_draft(),
            creative_brief=_brief(),
            config=DraftQualityConfig(enabled=False),
        )


def test_model_review_requires_fixed_unique_dimension_cards() -> None:
    review = _model_review()
    review["dimensions"] = review["dimensions"][:-1]

    with pytest.raises(ModelSelfReviewSchemaError):
        validate_model_self_review_output(
            task_input=_task_input(),
            content=review,
        )

    review = _model_review()
    review["dimensions"][1]["dimension"] = review["dimensions"][0]["dimension"]
    with pytest.raises(ModelSelfReviewSchemaError):
        validate_model_self_review_output(
            task_input=_task_input(),
            content=review,
        )


def test_model_review_task_rejects_facts_that_do_not_describe_the_exact_draft() -> None:
    task_input = _task_input()
    task_input["deterministic_quality_facts"]["script_character_count"] += 1  # type: ignore[index,operator]

    with pytest.raises(ModelSelfReviewSchemaError):
        validate_model_self_review_output(
            task_input=task_input,
            content=_model_review(),
        )


def test_unassessable_dimension_requires_limitation_without_fake_score() -> None:
    review = _model_review()
    dimension = review["dimensions"][0]
    dimension.update(
        {
            "assessable": False,
            "score": None,
            "limitation": "素材没有提供目标听众的反馈，无法可靠判断。",
            "evidence": [],
        }
    )

    validated = validate_model_self_review_output(
        task_input=_task_input(),
        content=review,
    )
    assert validated["dimensions"][0]["score"] is None

    invalid = deepcopy(review)
    invalid["dimensions"][0]["limitation"] = None
    with pytest.raises(ModelSelfReviewSchemaError):
        validate_model_self_review_output(
            task_input=_task_input(),
            content=invalid,
        )


def test_model_review_rejects_non_verbatim_evidence_and_out_of_scope_refs() -> None:
    with pytest.raises(InvalidModelReviewEvidence):
        validate_model_self_review_output(
            task_input=_task_input(),
            content=_model_review(quote="这句话不在任何指定的草稿块中"),
        )

    with pytest.raises(InvalidModelReviewSourceReference):
        validate_model_self_review_output(
            task_input=_task_input(),
            content=_model_review(
                source_ref={
                    "source_id": "src_outside",
                    "source_segment_id": "seg_outside",
                }
            ),
        )

    with pytest.raises(InvalidModelReviewSourceReference):
        validate_model_self_review_output(
            task_input=_task_input(),
            content=_model_review(source_ref=_reference(1)),
        )

    missing_grounding = _model_review()
    missing_grounding["dimensions"][1]["evidence"][0]["source_refs"] = []
    with pytest.raises(InvalidModelReviewSourceReference):
        validate_model_self_review_output(
            task_input=_task_input(),
            content=missing_grounding,
        )

    leaked_identifier = _model_review()
    leaked_identifier["dimensions"][0]["assessment"] = "这里不应该展示 src_internal_identifier。"
    with pytest.raises(InvalidModelReviewEvidence):
        validate_model_self_review_output(
            task_input=_task_input(),
            content=leaked_identifier,
        )


def test_report_is_code_owned_advisory_and_same_model_relation_is_visible() -> None:
    deterministic = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    )
    review = ModelSelfReviewOutput.model_validate(_model_review(score=4))

    report = build_draft_quality_report(
        deterministic=deterministic,
        model_self_review=review,
        editor_provider="deepseek",
        editor_model="deepseek-chat",
        reviewer_provider="deepseek",
        reviewer_model="deepseek-chat",
    )

    assert report.model_review_advisory is True
    assert report.reviewer_relation == "same_model"
    assert report.scoring_formula_version == "draft_quality_v2_non_compensatory_caps"
    assert report.experimental_model_score == 80
    assert report.experimental_uncapped_overall_score == round(
        deterministic.deterministic_score * 0.6 + 80 * 0.4,
        2,
    )
    assert report.code_owned_score_cap == 100
    assert report.score_cap_reasons == []
    assert report.experimental_overall_score == round(
        deterministic.deterministic_score * 0.6 + 80 * 0.4,
        2,
    )
    assert report.decision == "candidate_ready_for_human_review"
    assert report.requires_human_review is True


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("experimental_model_score", 99.0),
        ("experimental_uncapped_overall_score", 99.0),
        ("code_owned_score_cap", 39),
        ("experimental_overall_score", 80.0),
        ("decision", "blocked"),
    ],
)
def test_v2_report_rejects_inconsistent_scores_caps_and_decision(
    field_name: str,
    invalid_value: object,
) -> None:
    deterministic = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    )
    payload = build_draft_quality_report(
        deterministic=deterministic,
        model_self_review=ModelSelfReviewOutput.model_validate(_model_review(score=4)),
        editor_provider="deepseek",
        editor_model="deepseek-v4-flash",
        reviewer_provider="deepseek",
        reviewer_model="deepseek-v4-pro",
    ).model_dump(mode="python")
    payload[field_name] = invalid_value

    with pytest.raises(ValueError):
        DraftQualityReport.model_validate(payload)


def test_v2_report_rejects_cap_reasons_that_do_not_match_findings() -> None:
    deterministic = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    )
    payload = build_draft_quality_report(
        deterministic=deterministic,
        model_self_review=ModelSelfReviewOutput.model_validate(_model_review(score=4)),
    ).model_dump(mode="python")
    payload["score_cap_reasons"] = [
        {
            "code": "deterministic_blocker_cap",
            "cap": 39,
            "explanation": "伪造的上限原因。",
        }
    ]

    with pytest.raises(ValueError):
        DraftQualityReport.model_validate(payload)


def test_v2_report_rejects_fabricated_model_conflict() -> None:
    deterministic = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    )
    payload = build_draft_quality_report(
        deterministic=deterministic,
        model_self_review=ModelSelfReviewOutput.model_validate(_model_review(score=4)),
    ).model_dump(mode="python")
    payload["model_review_conflicts"] = [
        {
            "code": "duration_vs_brief_adherence_score",
            "dimension": "brief_adherence",
            "model_score": 4,
            "deterministic_finding_codes": ["duration.within_target_range"],
            "explanation": "伪造的模型与代码冲突。",
        }
    ]

    with pytest.raises(ValueError):
        DraftQualityReport.model_validate(payload)


def test_report_distinguishes_cross_tier_review_within_deepseek_v4() -> None:
    deterministic = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    )
    review = ModelSelfReviewOutput.model_validate(_model_review(score=4))

    report = build_draft_quality_report(
        deterministic=deterministic,
        model_self_review=review,
        editor_provider="deepseek",
        editor_model="deepseek-v4-flash",
        reviewer_provider="deepseek",
        reviewer_model="deepseek-v4-pro",
    )

    assert report.reviewer_relation == "cross_tier_same_family"


def test_short_ten_minute_draft_with_all_fives_is_capped_and_conflict_is_visible() -> None:
    content = _good_draft().model_dump(mode="python")
    blocks = [
        content["podcast_script"]["opening"],
        *[
            paragraph
            for section in content["podcast_script"]["sections"]
            for paragraph in section["paragraphs"]
        ],
        content["podcast_script"]["closing"],
    ]
    lengths = [80, 140, 210, 300, 400, 299]
    offset = 4_000
    for block, length in zip(blocks, lengths, strict=True):
        block["text"] = _unique_chinese(offset, length)
        offset += length + 50
    blocks[0]["text"] = "旧录音" + blocks[0]["text"][3:]
    blocks[-1]["text"] = "重新开始" + blocks[-1]["text"][4:]

    deterministic = analyze_podcast_draft(draft=content, creative_brief=_brief())
    report = build_draft_quality_report(
        deterministic=deterministic,
        model_self_review=ModelSelfReviewOutput.model_validate(_model_review(score=5)),
        editor_provider="deepseek",
        editor_model="deepseek-v4-flash",
        reviewer_provider="deepseek",
        reviewer_model="deepseek-v4-flash",
    )

    assert deterministic.metrics.script_character_count == 1_429
    assert deterministic.metrics.estimated_duration_minutes == 5.1
    assert deterministic.has_blocker is False
    assert deterministic.has_warning is True
    assert report.experimental_model_score == 100
    assert report.experimental_uncapped_overall_score is not None
    assert report.experimental_uncapped_overall_score >= 80
    assert report.code_owned_score_cap == 59
    assert report.experimental_overall_score == 59
    assert report.experimental_overall_score < 80
    assert report.decision == "revision_recommended"
    assert [reason.code for reason in report.score_cap_reasons] == [
        "duration_coverage_below_60_percent_cap",
        "deterministic_warning_cap",
    ]
    assert len(report.model_review_conflicts) == 1
    conflict = report.model_review_conflicts[0]
    assert conflict.code == "duration_vs_brief_adherence_score"
    assert conflict.dimension == "brief_adherence"
    assert conflict.model_score == 5
    assert conflict.deterministic_finding_codes == ["duration.outside_target_range"]

    missing_conflict = report.model_dump(mode="python")
    missing_conflict["model_review_conflicts"] = []
    with pytest.raises(ValueError):
        DraftQualityReport.model_validate(missing_conflict)


@pytest.mark.parametrize(
    ("character_count", "model_score", "expected_status", "maximum_allowed"),
    [
        (1_000, 3, "blocker", 2),
        (2_100, 4, "warning", 3),
    ],
)
def test_duration_conflict_threshold_matches_reviewer_prompt(
    character_count: int,
    model_score: int,
    expected_status: str,
    maximum_allowed: int,
) -> None:
    deterministic = analyze_podcast_draft(
        draft=_draft_with_script_character_count(character_count),
        creative_brief=CreativeBrief(
            target_duration_minutes=10,
            speaking_rate_chars_per_minute=280,
        ),
    )
    report = build_draft_quality_report(
        deterministic=deterministic,
        model_self_review=ModelSelfReviewOutput.model_validate(_model_review(score=model_score)),
    )

    duration_finding = next(
        finding for finding in deterministic.findings if finding.code.startswith("duration.")
    )
    assert duration_finding.status == expected_status
    assert len(report.model_review_conflicts) == 1
    conflict = report.model_review_conflicts[0]
    assert conflict.model_score == model_score
    assert f"最多支持 {maximum_allowed}/5" in conflict.explanation


def test_legacy_v1_quality_report_still_deserializes_without_v2_calibration_fields() -> None:
    deterministic = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    )
    legacy = build_draft_quality_report(
        deterministic=deterministic,
        model_self_review=ModelSelfReviewOutput.model_validate(_model_review(score=4)),
        scoring_formula_version=LEGACY_DRAFT_QUALITY_FORMULA_VERSION,
    ).model_dump(mode="python")
    for field in (
        "experimental_uncapped_overall_score",
        "code_owned_score_cap",
        "score_cap_reasons",
        "model_review_conflicts",
    ):
        legacy.pop(field)

    restored = DraftQualityReport.model_validate(legacy)

    assert restored.scoring_formula_version == LEGACY_DRAFT_QUALITY_FORMULA_VERSION
    assert restored.experimental_uncapped_overall_score is None
    assert restored.code_owned_score_cap is None
    assert restored.score_cap_reasons == []
    assert restored.model_review_conflicts == []
    assert restored.experimental_overall_score == round(
        deterministic.deterministic_score * 0.6 + 80 * 0.4,
        2,
    )

    legacy["experimental_overall_score"] = 99
    with pytest.raises(ValueError, match="experimental_overall_score"):
        DraftQualityReport.model_validate(legacy)


def test_objective_blocker_cannot_be_overridden_by_model_and_failure_degrades() -> None:
    content = _good_draft().model_dump(mode="python")
    for _, paragraph in [
        ("opening", content["podcast_script"]["opening"]),
        ("closing", content["podcast_script"]["closing"]),
    ]:
        paragraph["text"] = "短。"
    for section in content["podcast_script"]["sections"]:
        for paragraph in section["paragraphs"]:
            paragraph["text"] = "短。"
    blocked = analyze_podcast_draft(draft=content, creative_brief=_brief())

    report = build_draft_quality_report(
        deterministic=blocked,
        model_self_review=ModelSelfReviewOutput.model_validate(_model_review(score=5)),
        editor_provider="deepseek",
        editor_model="deepseek-chat",
        reviewer_provider="deepseek",
        reviewer_model="deepseek-chat",
    )
    assert report.decision == "blocked"
    blocked_without_reviewer = build_draft_quality_report(
        deterministic=blocked,
        model_self_review=None,
        unavailable_reason="provider_authentication_failed",
    )
    assert blocked_without_reviewer.decision == "blocked"
    assert blocked_without_reviewer.model_review_status == "unavailable"

    incomplete_blocked_review = _model_review(score=4)
    incomplete_blocked_review["dimensions"][0].update(
        {
            "assessable": False,
            "score": None,
            "limitation": "现有证据不足，无法可靠判断这一维度。",
            "evidence": [],
        }
    )
    blocked_with_partial_reviewer = build_draft_quality_report(
        deterministic=blocked,
        model_self_review=ModelSelfReviewOutput.model_validate(incomplete_blocked_review),
    )
    assert blocked_with_partial_reviewer.decision == "blocked"
    assert blocked_with_partial_reviewer.experimental_overall_score is None

    healthy = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    )
    degraded = build_draft_quality_report(
        deterministic=healthy,
        model_self_review=None,
    )
    assert degraded.decision == "automated_review_incomplete"
    assert degraded.experimental_model_score is None
    assert degraded.experimental_overall_score is None
    assert degraded.requires_human_review is True

    partial_review = _model_review(score=4)
    partial_review["dimensions"][0].update(
        {
            "assessable": False,
            "score": None,
            "limitation": "缺少真实听众反馈，无法可靠判断这一维度。",
            "evidence": [],
        }
    )
    partial = build_draft_quality_report(
        deterministic=healthy,
        model_self_review=ModelSelfReviewOutput.model_validate(partial_review),
    )
    assert partial.decision == "automated_review_incomplete"
    assert partial.experimental_model_score is None
    assert partial.model_self_review is not None


def test_v3_weights_ready_personal_style_without_bypassing_hard_caps() -> None:
    healthy = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    )
    review = ModelSelfReviewOutput.model_validate(
        _style_aware_model_review(base_score=3, personal_style_score=5)
    )
    report = build_draft_quality_report(
        deterministic=healthy,
        model_self_review=review,
        scoring_formula_version=STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION,
        writing_style_context_status="ready",
    )

    # Six base dimensions average 60; personal style contributes 30% of the
    # model component: 60 * 0.70 + 100 * 0.30 = 72.
    assert report.experimental_model_score == 72
    assert report.experimental_uncapped_overall_score == round(
        healthy.deterministic_score * 0.6 + 72 * 0.4,
        2,
    )
    assert report.writing_style_context_status == "ready"
    markdown = render_draft_quality_markdown(report.model_dump(mode="json"))
    assert "个人写作风格匹配：5/5" in markdown
    assert "写作样本证据" in markdown
    assert "个人风格占模型分的 30%" in markdown

    short_draft = _draft_with_script_character_count(200)
    blocked = analyze_podcast_draft(
        draft=short_draft,
        creative_brief=_brief(),
    )
    blocked_report = build_draft_quality_report(
        deterministic=blocked,
        model_self_review=ModelSelfReviewOutput.model_validate(
            _style_aware_model_review(base_score=5, personal_style_score=5)
        ),
        scoring_formula_version=STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION,
        writing_style_context_status="ready",
    )

    assert blocked_report.experimental_model_score == 100
    assert blocked_report.code_owned_score_cap == 39
    assert blocked_report.experimental_overall_score == 39
    assert blocked_report.decision == "blocked"


def test_v3_limited_style_keeps_six_dimension_score_and_markdown_is_explicit() -> None:
    deterministic = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    )
    report = build_draft_quality_report(
        deterministic=deterministic,
        model_self_review=ModelSelfReviewOutput.model_validate(_model_review(score=4)),
        scoring_formula_version=STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION,
        writing_style_context_status="limited",
    )

    assert report.experimental_model_score == 80
    markdown = render_draft_quality_markdown(report.model_dump(mode="json"))
    assert "样本量有限" in markdown
    assert "不评价、也不声称稿子是否像本人" in markdown
    assert "个人风格不可评估" in markdown


def test_pre_v3_formula_cannot_claim_ready_personal_style_context() -> None:
    deterministic = analyze_podcast_draft(
        draft=_good_draft(),
        creative_brief=_brief(),
    )
    with pytest.raises(ValueError, match="only the v3 formula"):
        build_draft_quality_report(
            deterministic=deterministic,
            model_self_review=ModelSelfReviewOutput.model_validate(_model_review(score=4)),
            writing_style_context_status="ready",
        )
