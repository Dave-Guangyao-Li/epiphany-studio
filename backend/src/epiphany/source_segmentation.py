from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256

from epiphany.ids import stable_id

DEFAULT_MAX_SEGMENT_CHARS = 1200
_PARAGRAPH_PATTERN = re.compile(r"\S(?:.*?\S)?(?=(?:\n[ \t]*\n+)|\Z)", re.DOTALL)
_BOUNDARY_CHARACTERS = ("\n", "。", "！", "？", ".", "!", "?", "；", ";", " ")


class EmptySourceText(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SegmentDraft:
    id: str
    position: int
    text: str
    char_start: int
    char_end: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class SegmentationResult:
    normalized_text: str
    content_sha256: str
    segments: tuple[SegmentDraft, ...]


def normalize_source_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.strip()


def segment_source_text(
    text: str,
    *,
    max_segment_chars: int = DEFAULT_MAX_SEGMENT_CHARS,
) -> SegmentationResult:
    if max_segment_chars < 100:
        raise ValueError("max_segment_chars must be at least 100")

    normalized = normalize_source_text(text)
    if not normalized:
        raise EmptySourceText("source text must contain non-whitespace characters")

    source_hash = sha256(normalized.encode("utf-8")).hexdigest()
    raw_segments: list[tuple[int, int, str]] = []
    for match in _PARAGRAPH_PATTERN.finditer(normalized):
        block_start, block_end = match.span()
        block = match.group(0)
        raw_segments.extend(
            _split_long_block(
                block,
                block_start=block_start,
                max_segment_chars=max_segment_chars,
            )
        )

    segments: list[SegmentDraft] = []
    for position, (char_start, char_end, segment_text) in enumerate(raw_segments):
        segment_hash = sha256(segment_text.encode("utf-8")).hexdigest()
        segments.append(
            SegmentDraft(
                id=stable_id("seg", f"{source_hash}:{position}:{segment_hash}"),
                position=position,
                text=segment_text,
                char_start=char_start,
                char_end=char_end,
                content_sha256=segment_hash,
            )
        )

    return SegmentationResult(
        normalized_text=normalized,
        content_sha256=source_hash,
        segments=tuple(segments),
    )


def _split_long_block(
    block: str,
    *,
    block_start: int,
    max_segment_chars: int,
) -> list[tuple[int, int, str]]:
    pieces: list[tuple[int, int, str]] = []
    cursor = 0
    while cursor < len(block):
        remaining = len(block) - cursor
        if remaining <= max_segment_chars:
            cut = len(block)
        else:
            cut = _choose_boundary(block, cursor=cursor, max_segment_chars=max_segment_chars)

        raw_piece = block[cursor:cut]
        leading_whitespace = len(raw_piece) - len(raw_piece.lstrip())
        trailing_whitespace = len(raw_piece) - len(raw_piece.rstrip())
        piece_start = cursor + leading_whitespace
        piece_end = cut - trailing_whitespace
        if piece_start < piece_end:
            pieces.append(
                (
                    block_start + piece_start,
                    block_start + piece_end,
                    block[piece_start:piece_end],
                )
            )

        cursor = cut
        while cursor < len(block) and block[cursor].isspace():
            cursor += 1
    return pieces


def _choose_boundary(block: str, *, cursor: int, max_segment_chars: int) -> int:
    hard_cut = min(cursor + max_segment_chars, len(block))
    minimum_cut = cursor + max_segment_chars // 2
    window = block[cursor:hard_cut]
    candidates: list[int] = []
    for character in _BOUNDARY_CHARACTERS:
        index = window.rfind(character)
        if index >= 0:
            candidates.append(cursor + index + 1)
    valid = [candidate for candidate in candidates if candidate >= minimum_cut]
    return max(valid, default=hard_cut)
