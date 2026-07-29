from __future__ import annotations

from copy import deepcopy

import pytest

from epiphany.draft_quality import (
    analyze_podcast_draft,
    build_draft_quality_report,
)
from epiphany.draft_quality_schemas import (
    REVIEW_DIMENSIONS,
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
    return {
        "task_kind": "review_podcast_draft",
        "draft_artifact_id": "art_draft",
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


def test_duplicate_windows_templates_and_brief_literals_are_warnings() -> None:
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
    assert "style.template_phrases" in warnings
    assert "style.not_but_pattern" in warnings


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
    assert report.scoring_formula_version == "draft_quality_v1_60_40"
    assert report.experimental_model_score == 80
    assert report.experimental_overall_score == round(
        deterministic.deterministic_score * 0.6 + 80 * 0.4,
        2,
    )
    assert report.decision == "candidate_ready_for_human_review"
    assert report.requires_human_review is True


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
