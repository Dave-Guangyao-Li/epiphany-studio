from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from hashlib import sha256
from typing import Any

from epiphany.writing_style_schemas import (
    MAX_STYLE_NON_WHITESPACE_CHARS,
    MAX_STYLE_SEGMENTS,
    MIN_READY_STYLE_CHARS,
    MIN_READY_STYLE_SENTENCES,
    WritingSampleKind,
    WritingStyleObservableStats,
    WritingStyleProfile,
    WritingStyleProvenance,
    WritingStyleReadiness,
    WritingStyleReference,
    WritingStyleSegmentInput,
    WritingStyleSegmentReference,
)

_NON_WHITESPACE_PATTERN = re.compile(r"\s+")
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"[。！？!?；;.\n]+")
_PARAGRAPH_BOUNDARY_PATTERN = re.compile(r"\n\s*\n+")


def build_writing_style_profile(
    *,
    reference: WritingStyleReference | dict[str, Any] | None,
    source_segments: list[WritingStyleSegmentInput | dict[str, Any]] | None = None,
) -> WritingStyleProfile | None:
    """Build an opt-in, deterministic style profile.

    ``None`` means the user did not opt in, so no profile is built. Supplying
    segments without an explicit consent contract is rejected rather than
    silently treating arbitrary Sources as style examples.
    """

    if reference is None:
        if source_segments:
            raise ValueError("style segments require an explicit writing style reference")
        return None

    parsed_reference = WritingStyleReference.model_validate(reference)
    parsed_segments = [
        WritingStyleSegmentInput.model_validate(segment) for segment in source_segments or []
    ]
    _validate_segment_scope(
        reference=parsed_reference,
        source_segments=parsed_segments,
    )

    selected = _select_segments(
        reference=parsed_reference,
        source_segments=parsed_segments,
    )
    sample_kinds = {sample.source_id: sample.sample_kind for sample in parsed_reference.samples}
    selected_references = [
        _segment_reference(segment=segment, sample_kind=sample_kinds[segment.source_id])
        for segment in selected
    ]
    stats = _observable_stats(
        selected_segments=selected,
        selected_references=selected_references,
    )
    gaps = []
    if stats.non_whitespace_char_count < MIN_READY_STYLE_CHARS:
        gaps.append("insufficient_non_whitespace_chars")
    if stats.sentence_count < MIN_READY_STYLE_SENTENCES:
        gaps.append("insufficient_sentences")
    readiness = WritingStyleReadiness(
        status="ready" if not gaps else "limited",
        observed_non_whitespace_char_count=stats.non_whitespace_char_count,
        observed_sentence_count=stats.sentence_count,
        gaps=gaps,
    )
    selection_sha256 = _selection_sha256(
        reference=parsed_reference,
        selected_segments=selected_references,
    )

    return WritingStyleProfile(
        readiness=readiness,
        selected_segments=selected_references,
        stats=stats,
        provenance=WritingStyleProvenance(
            requested_sample_count=len(parsed_reference.samples),
            candidate_segment_count=len(parsed_segments),
            selected_segment_count=len(selected_references),
            excluded_segment_count=len(parsed_segments) - len(selected_references),
            selected_source_count=len({item.source_id for item in selected_references}),
            selection_sha256=selection_sha256,
        ),
    )


def validate_writing_style_profile_segments(
    *,
    profile: WritingStyleProfile | dict[str, Any],
    source_segments: list[WritingStyleSegmentInput | dict[str, Any]],
) -> None:
    """Verify that ephemeral text is the exact selection described by a profile.

    The order, references, positions, hashes, and observable per-segment counts
    must all agree. This prevents a caller from attaching different personal
    text to an already-consented profile.
    """

    parsed_profile = WritingStyleProfile.model_validate(profile)
    parsed_segments = [
        WritingStyleSegmentInput.model_validate(segment) for segment in source_segments
    ]
    if len(parsed_profile.selected_segments) != len(parsed_segments):
        raise ValueError("style profile and segment selection lengths do not match")

    for expected, actual in zip(
        parsed_profile.selected_segments,
        parsed_segments,
        strict=True,
    ):
        actual_reference = _segment_reference(
            segment=actual,
            sample_kind=expected.sample_kind,
        )
        if actual_reference != expected:
            raise ValueError("style profile reference, hash, or text statistics do not match")


def _validate_segment_scope(
    *,
    reference: WritingStyleReference,
    source_segments: list[WritingStyleSegmentInput],
) -> None:
    allowed_source_ids = {sample.source_id for sample in reference.samples}
    outside_scope = sorted({segment.source_id for segment in source_segments} - allowed_source_ids)
    if outside_scope:
        raise ValueError("style segments must belong to explicitly referenced writing samples")

    segment_keys = [(segment.source_id, segment.source_segment_id) for segment in source_segments]
    if len(segment_keys) != len(set(segment_keys)):
        raise ValueError("style segments must have unique source and segment references")

    positions = [(segment.source_id, segment.position) for segment in source_segments]
    if len(positions) != len(set(positions)):
        raise ValueError("style segment positions must be unique within each source")


def _select_segments(
    *,
    reference: WritingStyleReference,
    source_segments: list[WritingStyleSegmentInput],
) -> list[WritingStyleSegmentInput]:
    by_source: dict[str, deque[WritingStyleSegmentInput]] = defaultdict(deque)
    for segment in sorted(
        source_segments,
        key=lambda item: (item.source_id, item.position, item.source_segment_id),
    ):
        by_source[segment.source_id].append(segment)

    selected: list[WritingStyleSegmentInput] = []
    selected_chars = 0
    while len(selected) < MAX_STYLE_SEGMENTS and any(by_source.values()):
        made_progress = False
        for sample in reference.samples:
            candidates = by_source[sample.source_id]
            if not candidates:
                continue
            candidate = candidates.popleft()
            candidate_chars = _non_whitespace_char_count(candidate.text)
            if selected_chars + candidate_chars > MAX_STYLE_NON_WHITESPACE_CHARS:
                continue
            selected.append(candidate)
            selected_chars += candidate_chars
            made_progress = True
            if len(selected) >= MAX_STYLE_SEGMENTS:
                break
        if not made_progress and not any(by_source.values()):
            break
    return selected


def _segment_reference(
    *,
    segment: WritingStyleSegmentInput,
    sample_kind: WritingSampleKind,
) -> WritingStyleSegmentReference:
    sentence_lengths = _sentence_lengths(segment.text)
    return WritingStyleSegmentReference(
        source_id=segment.source_id,
        source_segment_id=segment.source_segment_id,
        position=segment.position,
        sample_kind=sample_kind,
        content_sha256=sha256(segment.text.encode("utf-8")).hexdigest(),
        non_whitespace_char_count=_non_whitespace_char_count(segment.text),
        sentence_count=len(sentence_lengths),
        paragraph_count=_paragraph_count(segment.text),
    )


def _observable_stats(
    *,
    selected_segments: list[WritingStyleSegmentInput],
    selected_references: list[WritingStyleSegmentReference],
) -> WritingStyleObservableStats:
    sentence_lengths = [
        length for segment in selected_segments for length in _sentence_lengths(segment.text)
    ]
    return WritingStyleObservableStats(
        source_count=len({segment.source_id for segment in selected_segments}),
        segment_count=len(selected_segments),
        non_whitespace_char_count=sum(
            segment.non_whitespace_char_count for segment in selected_references
        ),
        sentence_count=sum(segment.sentence_count for segment in selected_references),
        paragraph_count=sum(segment.paragraph_count for segment in selected_references),
        written_prose_segment_count=sum(
            segment.sample_kind == "written_prose" for segment in selected_references
        ),
        spoken_transcript_segment_count=sum(
            segment.sample_kind == "spoken_transcript" for segment in selected_references
        ),
        average_sentence_char_count=(
            round(sum(sentence_lengths) / len(sentence_lengths), 2) if sentence_lengths else None
        ),
        minimum_sentence_char_count=min(sentence_lengths, default=None),
        maximum_sentence_char_count=max(sentence_lengths, default=None),
        question_mark_count=sum(
            segment.text.count("?") + segment.text.count("？") for segment in selected_segments
        ),
        exclamation_mark_count=sum(
            segment.text.count("!") + segment.text.count("！") for segment in selected_segments
        ),
    )


def _selection_sha256(
    *,
    reference: WritingStyleReference,
    selected_segments: list[WritingStyleSegmentReference],
) -> str:
    stable_payload = {
        "reference": reference.model_dump(mode="json"),
        "selected_segments": [
            {
                "source_id": segment.source_id,
                "source_segment_id": segment.source_segment_id,
                "position": segment.position,
                "sample_kind": segment.sample_kind,
                "content_sha256": segment.content_sha256,
            }
            for segment in selected_segments
        ],
    }
    canonical = json.dumps(
        stable_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _non_whitespace_char_count(text: str) -> int:
    return len(_NON_WHITESPACE_PATTERN.sub("", text))


def _sentence_lengths(text: str) -> list[int]:
    return [
        length
        for raw_sentence in _SENTENCE_BOUNDARY_PATTERN.split(text)
        if (length := _non_whitespace_char_count(raw_sentence)) > 0
    ]


def _paragraph_count(text: str) -> int:
    return sum(
        1
        for paragraph in _PARAGRAPH_BOUNDARY_PATTERN.split(text)
        if _non_whitespace_char_count(paragraph) > 0
    )
