from __future__ import annotations

import html
import re
from collections.abc import Iterator, Mapping
from typing import Any

from epiphany.editor_schemas import (
    PodcastDraftOutput,
    PodcastScript,
    PodcastShowNotes,
    SourceReferenceKey,
)
from epiphany.interview_markdown import (
    MissingSourceCitation,
    RawSourceIdentifierInMarkdown,
    SourceCitation,
)
from epiphany.schemas import SourceReference

_MARKDOWN_CONTROL = re.compile(r"([\\`*_[\]{}()#+\-.!|>])")
_INTERNAL_SOURCE_IDENTIFIER = re.compile(r"(?:src|seg)_[A-Za-z0-9][A-Za-z0-9_-]*")


def _safe_text(value: str) -> str:
    html_escaped = html.escape(value, quote=False)
    return _MARKDOWN_CONTROL.sub(r"\\\1", html_escaped)


def _safe_single_line(value: str) -> str:
    return _safe_text(" ".join(value.split()))


def _reference_key(reference: SourceReference) -> SourceReferenceKey:
    return reference.source_id, reference.source_segment_id


def _iter_script_references(script: PodcastScript) -> Iterator[SourceReference]:
    yield from script.opening.source_refs
    for section in script.sections:
        yield from section.source_refs
        for paragraph in section.paragraphs:
            yield from paragraph.source_refs
    yield from script.closing.source_refs


def _iter_show_notes_references(
    show_notes: PodcastShowNotes,
) -> Iterator[SourceReference]:
    yield from show_notes.summary.source_refs
    for key_point in show_notes.key_points:
        yield from key_point.source_refs


def _citation_labels(
    references: Iterator[SourceReference],
    source_citations: Mapping[SourceReferenceKey, SourceCitation],
) -> tuple[dict[SourceReferenceKey, str], list[SourceReferenceKey]]:
    labels: dict[SourceReferenceKey, str] = {}
    ordered_keys: list[SourceReferenceKey] = []
    for reference in references:
        key = _reference_key(reference)
        if key not in source_citations:
            raise MissingSourceCitation("podcast export source metadata is missing")
        if key not in labels:
            labels[key] = f"S{len(labels) + 1}"
            ordered_keys.append(key)
    return labels, ordered_keys


def _source_line(
    references: list[SourceReference],
    labels: Mapping[SourceReferenceKey, str],
) -> str:
    return "来源：" + "、".join(
        f"[{labels[_reference_key(reference)]}]" for reference in references
    )


def _append_source_index(
    lines: list[str],
    *,
    labels: Mapping[SourceReferenceKey, str],
    ordered_keys: list[SourceReferenceKey],
    source_citations: Mapping[SourceReferenceKey, SourceCitation],
) -> None:
    lines.extend(["## 来源索引", ""])
    for key in ordered_keys:
        citation = source_citations[key]
        lines.append(
            f"- [{labels[key]}] 《{_safe_single_line(citation.title)}》"
            f"片段 {citation.segment_position + 1}"
        )
    lines.append("")


def contains_internal_source_identifier(markdown: str) -> bool:
    """Detect any source/segment ID after undoing Markdown backslash escapes."""

    # `_safe_text` escapes underscores, so an internal ID such as `src_deadbeef`
    # is serialized as `src\_deadbeef`. Removing all backslashes is deliberately
    # conservative: a model-provided literal backslash must not become an escape
    # hatch for leaking an otherwise recognizable internal identifier.
    visible_markdown = markdown.replace("\\", "")
    return _INTERNAL_SOURCE_IDENTIFIER.search(visible_markdown) is not None


def _reject_internal_ids(markdown: str) -> None:
    if contains_internal_source_identifier(markdown):
        raise RawSourceIdentifierInMarkdown(
            "podcast export natural language contains an internal source identifier"
        )


def render_podcast_draft_markdown(
    content: dict[str, Any],
    *,
    source_citations: Mapping[SourceReferenceKey, SourceCitation],
) -> str:
    output = PodcastDraftOutput.model_validate(content)
    labels, ordered_keys = _citation_labels(
        _iter_script_references(output.podcast_script),
        source_citations,
    )
    lines = [
        f"# {_safe_text(output.title)}",
        "",
        "## 开场",
        "",
        _safe_text(output.podcast_script.opening.text),
        "",
        _source_line(output.podcast_script.opening.source_refs, labels),
        "",
    ]
    for index, section in enumerate(output.podcast_script.sections, start=1):
        lines.extend(
            [
                f"## {index}. {_safe_text(section.title)}",
                "",
                _source_line(section.source_refs, labels),
                "",
            ]
        )
        for paragraph in section.paragraphs:
            lines.extend(
                [
                    _safe_text(paragraph.text),
                    "",
                    _source_line(paragraph.source_refs, labels),
                    "",
                ]
            )
    lines.extend(
        [
            "## 收束",
            "",
            _safe_text(output.podcast_script.closing.text),
            "",
            _source_line(output.podcast_script.closing.source_refs, labels),
            "",
        ]
    )
    _append_source_index(
        lines,
        labels=labels,
        ordered_keys=ordered_keys,
        source_citations=source_citations,
    )
    markdown = "\n".join(lines)
    _reject_internal_ids(markdown)
    return markdown


def render_show_notes_markdown(
    content: dict[str, Any],
    *,
    source_citations: Mapping[SourceReferenceKey, SourceCitation],
) -> str:
    output = PodcastDraftOutput.model_validate(content)
    labels, ordered_keys = _citation_labels(
        _iter_show_notes_references(output.show_notes),
        source_citations,
    )
    lines = [
        f"# {_safe_text(output.title)}｜Show Notes",
        "",
        _safe_text(output.show_notes.summary.text),
        "",
        _source_line(output.show_notes.summary.source_refs, labels),
        "",
        "## 本期内容",
        "",
    ]
    for key_point in output.show_notes.key_points:
        lines.extend(
            [
                f"- {_safe_text(key_point.text)}",
                f"  - {_source_line(key_point.source_refs, labels)}",
            ]
        )
    lines.append("")
    _append_source_index(
        lines,
        labels=labels,
        ordered_keys=ordered_keys,
        source_citations=source_citations,
    )
    markdown = "\n".join(lines)
    _reject_internal_ids(markdown)
    return markdown
