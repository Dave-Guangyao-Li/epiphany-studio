from __future__ import annotations

import pytest

from epiphany.source_segmentation import EmptySourceText, segment_source_text


def test_segmentation_normalizes_newlines_and_preserves_offsets() -> None:
    result = segment_source_text("  第一段。\r\n\r\n第二段有更多内容。\r\n仍然属于第二段。  ")

    assert result.normalized_text == "第一段。\n\n第二段有更多内容。\n仍然属于第二段。"
    assert [segment.position for segment in result.segments] == [0, 1]
    assert [segment.text for segment in result.segments] == [
        "第一段。",
        "第二段有更多内容。\n仍然属于第二段。",
    ]
    for segment in result.segments:
        assert result.normalized_text[segment.char_start : segment.char_end] == segment.text


def test_segmentation_is_deterministic_and_bounds_long_segments() -> None:
    text = f"{'A' * 60}。{'B' * 60}。{'C' * 60}。"

    first = segment_source_text(text, max_segment_chars=100)
    second = segment_source_text(text, max_segment_chars=100)

    assert first == second
    assert len(first.segments) == 3
    assert all(len(segment.text) <= 100 for segment in first.segments)


def test_segmentation_rejects_blank_source() -> None:
    with pytest.raises(EmptySourceText):
        segment_source_text(" \n\t ")
