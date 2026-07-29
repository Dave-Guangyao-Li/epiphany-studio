from __future__ import annotations

import html
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from epiphany.interview_schemas import InterviewScaffoldOutput
from epiphany.schemas import SourceReference

_MARKDOWN_CONTROL = re.compile(r"([\\`*_[\]{}()#+\-.!|>])")
SourceReferenceKey = tuple[str, str]


@dataclass(frozen=True)
class SourceCitation:
    """Human-readable metadata for one durable SourceSegment reference."""

    title: str
    segment_position: int


class MissingSourceCitation(ValueError):
    """Raised when an Artifact reference cannot be resolved to Source metadata."""


class RawSourceIdentifierInMarkdown(ValueError):
    """Raised when a human-facing export would expose an internal source ID."""


def _safe_text(value: str) -> str:
    """Render model text as inert inline Markdown, never as new structure or HTML."""

    html_escaped = html.escape(value, quote=False)
    return _MARKDOWN_CONTROL.sub(r"\\\1", html_escaped)


def _safe_single_line(value: str) -> str:
    return _safe_text(" ".join(value.split()))


def _reference_key(reference: SourceReference) -> SourceReferenceKey:
    return reference.source_id, reference.source_segment_id


def _iter_references(scaffold: InterviewScaffoldOutput) -> Iterator[SourceReference]:
    yield from scaffold.episode_intent.source_refs
    yield from scaffold.opening.source_refs
    for section in scaffold.sections:
        yield from section.source_refs
        for statement in section.known_context:
            yield from statement.source_refs
        yield from section.transition.source_refs
        for question in section.questions:
            yield from question.source_refs
    for gap in scaffold.material_gaps:
        yield from gap.source_refs
    yield from scaffold.closing.source_refs


def interview_scaffold_reference_keys(
    content: dict[str, Any],
) -> tuple[SourceReferenceKey, ...]:
    """Return unique references in the same stable order used by the Markdown."""

    scaffold = InterviewScaffoldOutput.model_validate(content)
    ordered: list[SourceReferenceKey] = []
    seen: set[SourceReferenceKey] = set()
    for reference in _iter_references(scaffold):
        key = _reference_key(reference)
        if key not in seen:
            ordered.append(key)
            seen.add(key)
    return tuple(ordered)


def _citation_labels(
    scaffold: InterviewScaffoldOutput,
    source_citations: Mapping[SourceReferenceKey, SourceCitation],
) -> tuple[dict[SourceReferenceKey, str], list[SourceReferenceKey]]:
    labels: dict[SourceReferenceKey, str] = {}
    ordered_keys: list[SourceReferenceKey] = []
    for reference in _iter_references(scaffold):
        key = _reference_key(reference)
        if key not in source_citations:
            raise MissingSourceCitation("interview scaffold source metadata is missing")
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


def render_interview_scaffold_markdown(
    content: dict[str, Any],
    *,
    source_citations: Mapping[SourceReferenceKey, SourceCitation],
) -> str:
    scaffold = InterviewScaffoldOutput.model_validate(content)
    citation_labels, ordered_citation_keys = _citation_labels(scaffold, source_citations)
    lines = [
        f"# {_safe_text(scaffold.title)}",
        "",
        f"> {_safe_text(scaffold.episode_intent.text)}",
        "",
        _source_line(scaffold.episode_intent.source_refs, citation_labels),
        "",
        "## 开场",
        "",
        _safe_text(scaffold.opening.text),
        "",
        _source_line(scaffold.opening.source_refs, citation_labels),
        "",
    ]

    for index, section in enumerate(scaffold.sections, start=1):
        lines.extend(
            [
                f"## {index}. {_safe_text(section.title)}",
                "",
                _source_line(section.source_refs, citation_labels),
                "",
                "### 已知背景",
                "",
            ]
        )
        for statement in section.known_context:
            lines.extend(
                [
                    f"- {_safe_text(statement.text)}",
                    f"  - {_source_line(statement.source_refs, citation_labels)}",
                ]
            )

        lines.extend(
            [
                "",
                "### 可直接说的过渡",
                "",
                f"> {_safe_text(section.transition.text)}",
                "",
                _source_line(section.transition.source_refs, citation_labels),
                "",
                "### 采访问题",
                "",
            ]
        )
        for question_index, question in enumerate(section.questions, start=1):
            lines.extend(
                [
                    f"{question_index}. {_safe_text(question.prompt)}",
                    f"   - 追问目的：{_safe_text(question.purpose)}",
                    f"   - 关键词：{' / '.join(_safe_text(item) for item in question.keywords)}",
                    f"   - {_source_line(question.source_refs, citation_labels)}",
                ]
            )
        lines.append("")

    if scaffold.material_gaps:
        lines.extend(["## 还缺少的素材", ""])
        for gap in scaffold.material_gaps:
            lines.extend(
                [
                    f"- {_safe_text(gap.gap)}",
                    f"  - 为什么值得补充：{_safe_text(gap.why_it_matters)}",
                    f"  - {_source_line(gap.source_refs, citation_labels)}",
                ]
            )
        lines.append("")

    lines.extend(
        [
            "## 收束",
            "",
            _safe_text(scaffold.closing.text),
            "",
            _source_line(scaffold.closing.source_refs, citation_labels),
            "",
            "## 来源索引",
            "",
        ]
    )
    for key in ordered_citation_keys:
        citation = source_citations[key]
        lines.append(
            f"- [{citation_labels[key]}] 《{_safe_single_line(citation.title)}》"
            f"片段 {citation.segment_position + 1}"
        )
    lines.append("")
    markdown = "\n".join(lines)
    visible_markdown = markdown.replace("\\", "")
    if any(
        identifier in visible_markdown
        for source_id, source_segment_id in ordered_citation_keys
        for identifier in (source_id, source_segment_id)
    ):
        raise RawSourceIdentifierInMarkdown(
            "interview scaffold natural language contains an internal source identifier"
        )
    return markdown
