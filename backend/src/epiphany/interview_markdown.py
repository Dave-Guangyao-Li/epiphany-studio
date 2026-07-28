from __future__ import annotations

import html
import re
from typing import Any

from epiphany.interview_schemas import InterviewScaffoldOutput
from epiphany.schemas import SourceReference

_MARKDOWN_CONTROL = re.compile(r"([\\`*_[\]{}()#+\-.!|>])")


def _safe_text(value: str) -> str:
    """Render model text as inert inline Markdown, never as new structure or HTML."""

    html_escaped = html.escape(value, quote=False)
    return _MARKDOWN_CONTROL.sub(r"\\\1", html_escaped)


def _source_label(reference: SourceReference) -> str:
    return f"`{reference.source_id}#{reference.source_segment_id}`"


def _source_line(references: list[SourceReference]) -> str:
    return "来源：" + "、".join(_source_label(reference) for reference in references)


def render_interview_scaffold_markdown(content: dict[str, Any]) -> str:
    scaffold = InterviewScaffoldOutput.model_validate(content)
    lines = [
        f"# {_safe_text(scaffold.title)}",
        "",
        f"> {_safe_text(scaffold.episode_intent.text)}",
        "",
        _source_line(scaffold.episode_intent.source_refs),
        "",
        "## 开场",
        "",
        _safe_text(scaffold.opening.text),
        "",
        _source_line(scaffold.opening.source_refs),
        "",
    ]

    for index, section in enumerate(scaffold.sections, start=1):
        lines.extend(
            [
                f"## {index}. {_safe_text(section.title)}",
                "",
                _source_line(section.source_refs),
                "",
                "### 已知背景",
                "",
            ]
        )
        for statement in section.known_context:
            lines.extend(
                [
                    f"- {_safe_text(statement.text)}",
                    f"  - {_source_line(statement.source_refs)}",
                ]
            )

        lines.extend(
            [
                "",
                "### 可直接说的过渡",
                "",
                f"> {_safe_text(section.transition.text)}",
                "",
                _source_line(section.transition.source_refs),
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
                    f"   - {_source_line(question.source_refs)}",
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
                    f"  - {_source_line(gap.source_refs)}",
                ]
            )
        lines.append("")

    lines.extend(
        [
            "## 收束",
            "",
            _safe_text(scaffold.closing.text),
            "",
            _source_line(scaffold.closing.source_refs),
            "",
        ]
    )
    return "\n".join(lines)
