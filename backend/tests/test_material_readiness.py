from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from epiphany.material_readiness import assess_material_readiness
from epiphany.quality_contract_schemas import CreativeBrief


def _segment(
    source: str,
    segment: str,
    text: str,
) -> dict[str, str]:
    return {
        "source_id": source,
        "source_segment_id": segment,
        "text": text,
    }


def _question(source: str, segment: str) -> dict[str, object]:
    return {
        "prompt": "请补充一个当时真实发生的具体场景。",
        "purpose": "补足动作、感受和转折。",
        "source_refs": [
            {
                "source_id": source,
                "source_segment_id": segment,
            }
        ],
    }


def test_creative_brief_resolves_defaults_and_normalizes_lists() -> None:
    brief = CreativeBrief.model_validate(
        {
            "target_audience": "  未来的自己   和相似阶段的听众 ",
            "communication_goal": "  回答   为什么重新开始记录 ",
            "tone": [" 真诚 ", "自然  口语"],
            "must_include": ["三秒停顿"],
            "avoid_patterns": [" 强行   金句 "],
        }
    )

    assert brief.target_duration_minutes == 10
    assert brief.speaking_rate_chars_per_minute == 280
    assert brief.scenario == "reflective_solo"
    assert brief.target_audience == "未来的自己 和相似阶段的听众"
    assert brief.communication_goal == "回答 为什么重新开始记录"
    assert brief.tone == ["真诚", "自然 口语"]
    assert brief.avoid_patterns == ["强行 金句"]


@pytest.mark.parametrize(
    "patch",
    [
        {"target_duration_minutes": 20},
        {"speaking_rate_chars_per_minute": 119},
        {"target_audience": " \n "},
        {"communication_goal": "\t"},
        {"tone": []},
        {"tone": ["真诚", " 真诚 "]},
        {"unexpected": True},
    ],
)
def test_creative_brief_rejects_invalid_contract(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CreativeBrief.model_validate(patch)


def test_ten_minute_report_is_ready_at_exact_lower_bound() -> None:
    report = assess_material_readiness(
        creative_brief=CreativeBrief(target_duration_minutes=10),
        initial_source_segments=[
            _segment("src_initial", "seg_initial", "甲" * 1_400),
        ],
        supplemental_source_segments=[
            _segment("src_supplement", "seg_supplement", "乙" * 980),
        ],
        follow_up_questions=[_question("src_initial", "seg_initial")],
    )

    assert report.status == "ready"
    assert report.target_script_char_count == 2_800
    assert report.target_script_chars_min == 2_380
    assert report.target_script_chars_max == 3_220
    assert report.counts.available_source_char_count == 2_380
    assert report.additional_source_chars_needed == 0
    assert report.gaps == []
    assert report.follow_up_questions == []


def test_same_material_is_insufficient_for_thirty_minutes_and_keeps_questions() -> None:
    source_text = "这段原始素材正文绝不能出现在报告里"
    supplemental_text = "补充口述同样不能复制进报告"
    report = assess_material_readiness(
        creative_brief=CreativeBrief(target_duration_minutes=30),
        initial_source_segments=[
            _segment("src_initial", "seg_initial", source_text * 20),
        ],
        supplemental_source_segments=[
            _segment("src_supplement", "seg_supplement", supplemental_text * 20),
        ],
        follow_up_questions=[
            _question("src_initial", "seg_initial"),
            _question("src_initial", "seg_initial"),
        ],
    )

    serialized = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert report.status == "needs_more_material"
    assert report.checks.enough_evidence_chars is False
    assert report.additional_source_chars_needed > 0
    assert [gap.code for gap in report.gaps] == ["insufficient_evidence_volume"]
    assert len(report.follow_up_questions) == 1
    assert report.follow_up_questions[0].source_refs[0].source_id == "src_initial"
    assert source_text not in serialized
    assert supplemental_text not in serialized
    assert "src_initial" in serialized


def test_duplicate_and_overlapping_segments_are_not_double_counted() -> None:
    duplicated = _segment("src_initial", "seg_shared", "甲 乙\n丙")
    report = assess_material_readiness(
        creative_brief=CreativeBrief(target_duration_minutes=10),
        initial_source_segments=[duplicated, duplicated],
        supplemental_source_segments=[
            duplicated,
            _segment("src_supplement", "seg_new", "丁 戊"),
        ],
    )

    assert report.counts.initial_segment_count == 1
    assert report.counts.initial_char_count == 3
    assert report.counts.supplemental_segment_count == 1
    assert report.counts.supplemental_char_count == 2
    assert report.counts.available_source_char_count == 5
    assert report.counts.duplicate_segment_count == 2


def test_same_text_in_different_sources_does_not_fake_volume_or_diversity() -> None:
    report = assess_material_readiness(
        creative_brief=CreativeBrief(target_duration_minutes=10),
        initial_source_segments=[
            _segment("src_initial", "seg_initial", "同一段 具体素材"),
        ],
        supplemental_source_segments=[
            _segment("src_copy", "seg_copy", "同一段\n具体素材"),
        ],
    )

    assert report.counts.initial_char_count == 7
    assert report.counts.supplemental_char_count == 0
    assert report.counts.duplicate_segment_count == 1
    assert report.checks.has_supplemental_material is False
    assert report.checks.has_source_diversity is False
    assert report.status == "needs_more_material"


def test_missing_supplement_is_reported_without_copying_initial_text() -> None:
    text = "只存在于临时计算输入中的私密原文"
    report = assess_material_readiness(
        creative_brief=CreativeBrief(),
        initial_source_segments=[
            _segment("src_initial", "seg_initial", text * 200),
        ],
        supplemental_source_segments=[],
    )

    serialized = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    gap_codes = {gap.code for gap in report.gaps}
    assert report.status == "needs_more_material"
    assert "missing_supplemental_material" in gap_codes
    assert "limited_source_diversity" in gap_codes
    assert text not in serialized


def test_follow_up_question_must_reference_an_input_segment() -> None:
    with pytest.raises(
        ValidationError,
        match="follow-up question references must resolve",
    ):
        assess_material_readiness(
            creative_brief=CreativeBrief(),
            initial_source_segments=[
                _segment("src_initial", "seg_initial", "初始素材"),
            ],
            supplemental_source_segments=[
                _segment("src_supplement", "seg_supplement", "补充素材"),
            ],
            follow_up_questions=[
                _question("src_outside", "seg_outside"),
            ],
        )


def test_custom_speaking_rate_changes_target_character_contract() -> None:
    report = assess_material_readiness(
        creative_brief=CreativeBrief(
            target_duration_minutes=15,
            speaking_rate_chars_per_minute=200,
        ),
        initial_source_segments=[
            _segment("src_initial", "seg_initial", "甲" * 1_500),
        ],
        supplemental_source_segments=[
            _segment("src_supplement", "seg_supplement", "乙" * 1_050),
        ],
    )

    assert report.target_script_char_count == 3_000
    assert report.target_script_chars_min == 2_550
    assert report.target_script_chars_max == 3_450
    assert report.status == "ready"


def test_readiness_calculator_accepts_more_than_500_initial_segments() -> None:
    report = assess_material_readiness(
        creative_brief=CreativeBrief(target_duration_minutes=30),
        initial_source_segments=[
            _segment("src_initial", f"seg_{index}", f"甲{index}") for index in range(501)
        ],
        supplemental_source_segments=[],
    )

    assert report.counts.initial_segment_count == 501
    assert report.status == "needs_more_material"
