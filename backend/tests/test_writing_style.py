from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from epiphany.writing_style import build_writing_style_profile
from epiphany.writing_style_schemas import (
    MAX_STYLE_NON_WHITESPACE_CHARS,
    MAX_STYLE_SEGMENTS,
    WritingStyleReference,
)


def _reference(
    *samples: tuple[str, str],
) -> dict[str, object]:
    return {
        "version": "writing_style_reference_v1",
        "samples": [
            {"source_id": source_id, "sample_kind": sample_kind}
            for source_id, sample_kind in samples
        ],
        "ownership_attested": True,
        "model_processing_consent": True,
        "usage": "style_only",
    }


def _segment(
    source_id: str,
    position: int,
    text: str,
) -> dict[str, object]:
    return {
        "source_id": source_id,
        "source_segment_id": f"seg_{source_id}_{position}",
        "position": position,
        "text": text,
    }


def test_style_profile_requires_explicit_opt_in() -> None:
    assert build_writing_style_profile(reference=None) is None

    with pytest.raises(ValueError, match="explicit writing style reference"):
        build_writing_style_profile(
            reference=None,
            source_segments=[_segment("src_private", 0, "不应被静默处理。")],
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"ownership_attested": False},
        {"model_processing_consent": False},
        {"usage": "factual_evidence"},
        {"version": "writing_style_reference_v2"},
        {
            "samples": [
                {"source_id": "src_same", "sample_kind": "written_prose"},
                {"source_id": "src_same", "sample_kind": "spoken_transcript"},
            ]
        },
        {
            "samples": [
                {"source_id": f"src_{index}", "sample_kind": "written_prose"} for index in range(6)
            ]
        },
    ],
)
def test_reference_rejects_missing_consent_or_invalid_scope(
    patch: dict[str, object],
) -> None:
    payload = _reference(("src_article", "written_prose"))
    payload.update(patch)

    with pytest.raises(ValidationError):
        WritingStyleReference.model_validate(payload)


def test_profile_is_deterministic_round_robin_and_contains_no_sample_text() -> None:
    private_phrase = "只有原文里才有的私密句子"
    reference = _reference(
        ("src_article", "written_prose"),
        ("src_voice", "spoken_transcript"),
    )
    segments = [
        _segment(
            "src_article",
            1,
            f"第二段。这里保留自然的写作节奏。{private_phrase}！还有最后一句？",
        ),
        _segment("src_voice", 1, "第二段口述。我会停一下。然后再往下讲。"),
        _segment("src_voice", 0, "第一段口述。这个事情要从一个下午说起。"),
        _segment("src_article", 0, "第一段文章。它先给出一个具体场景。"),
    ]
    first = build_writing_style_profile(
        reference=reference,
        source_segments=segments,
    )
    second = build_writing_style_profile(
        reference=reference,
        source_segments=list(reversed(segments)),
    )

    assert first is not None
    assert second is not None
    assert first == second
    assert [(item.source_id, item.position) for item in first.selected_segments] == [
        ("src_article", 0),
        ("src_voice", 0),
        ("src_article", 1),
        ("src_voice", 1),
    ]
    assert first.provenance.selection_sha256 == second.provenance.selection_sha256
    assert first.safety.input_trust == "untrusted"
    assert first.safety.usage == "style_only"
    assert first.safety.may_supply_factual_evidence is False
    assert first.safety.may_supply_instructions is False
    serialized = json.dumps(first.model_dump(mode="json"), ensure_ascii=False)
    assert private_phrase not in serialized
    assert '"text"' not in serialized


def test_profile_enforces_segment_and_character_caps() -> None:
    reference = _reference(
        ("src_a", "written_prose"),
        ("src_b", "spoken_transcript"),
    )
    segments = [
        _segment(
            "src_a" if index % 2 == 0 else "src_b",
            index // 2,
            f"{index:02d}" + "甲" * 698,
        )
        for index in range(30)
    ]
    profile = build_writing_style_profile(
        reference=reference,
        source_segments=segments,
    )

    assert profile is not None
    assert len(profile.selected_segments) == 17
    assert profile.stats.non_whitespace_char_count <= MAX_STYLE_NON_WHITESPACE_CHARS
    assert profile.provenance.candidate_segment_count == 30
    assert profile.provenance.selected_segment_count == len(profile.selected_segments)
    assert profile.provenance.excluded_segment_count == 30 - len(profile.selected_segments)


def test_profile_selects_at_most_twenty_short_segments() -> None:
    profile = build_writing_style_profile(
        reference=_reference(("src_many", "written_prose")),
        source_segments=[
            _segment("src_many", index, f"{index:02d}" + "甲" * 98) for index in range(30)
        ],
    )

    assert profile is not None
    assert len(profile.selected_segments) == MAX_STYLE_SEGMENTS
    assert [segment.position for segment in profile.selected_segments] == list(
        range(MAX_STYLE_SEGMENTS)
    )
    assert profile.provenance.excluded_segment_count == 10


def test_readiness_requires_both_character_volume_and_five_sentences() -> None:
    ready_text = "甲" * 160 + "。" + "乙" * 160 + "。" + "丙" * 160 + "。"
    ready_text += "丁" * 160 + "。" + "戊" * 160 + "。"
    ready = build_writing_style_profile(
        reference=_reference(("src_ready", "written_prose")),
        source_segments=[_segment("src_ready", 0, ready_text)],
    )
    too_few_sentences = build_writing_style_profile(
        reference=_reference(("src_long", "written_prose")),
        source_segments=[_segment("src_long", 0, "甲" * 1_000 + "。")],
    )
    too_few_chars = build_writing_style_profile(
        reference=_reference(("src_short", "spoken_transcript")),
        source_segments=[_segment("src_short", 0, "一。二。三。四。五。")],
    )

    assert ready is not None
    assert too_few_sentences is not None
    assert too_few_chars is not None
    assert ready.readiness.status == "ready"
    assert ready.readiness.gaps == []
    assert too_few_sentences.readiness.status == "limited"
    assert too_few_sentences.readiness.gaps == ["insufficient_sentences"]
    assert too_few_chars.readiness.status == "limited"
    assert too_few_chars.readiness.gaps == ["insufficient_non_whitespace_chars"]


def test_selection_hash_changes_when_selected_content_changes() -> None:
    reference = _reference(("src_sample", "written_prose"))
    first = build_writing_style_profile(
        reference=reference,
        source_segments=[_segment("src_sample", 0, "第一句。第二句。")],
    )
    second = build_writing_style_profile(
        reference=reference,
        source_segments=[_segment("src_sample", 0, "第一句。已经改变的第二句。")],
    )

    assert first is not None
    assert second is not None
    assert first.provenance.selection_sha256 != second.provenance.selection_sha256
    assert first.selected_segments[0].content_sha256 != second.selected_segments[0].content_sha256


def test_segments_outside_explicit_scope_and_duplicate_positions_are_rejected() -> None:
    reference = _reference(("src_allowed", "written_prose"))

    with pytest.raises(ValueError, match="explicitly referenced"):
        build_writing_style_profile(
            reference=reference,
            source_segments=[_segment("src_other", 0, "不在授权范围。")],
        )

    with pytest.raises(ValueError, match="positions must be unique"):
        build_writing_style_profile(
            reference=reference,
            source_segments=[
                _segment("src_allowed", 0, "第一份。"),
                {
                    **_segment("src_allowed", 0, "第二份。"),
                    "source_segment_id": "seg_different",
                },
            ],
        )


def test_empty_referenced_source_produces_limited_zero_text_profile() -> None:
    profile = build_writing_style_profile(
        reference=_reference(("src_empty_selection", "written_prose")),
        source_segments=[],
    )

    assert profile is not None
    assert profile.readiness.status == "limited"
    assert profile.readiness.gaps == [
        "insufficient_non_whitespace_chars",
        "insufficient_sentences",
    ]
    assert profile.stats.non_whitespace_char_count == 0
    assert profile.selected_segments == []
    assert profile.provenance.selected_source_count == 0
