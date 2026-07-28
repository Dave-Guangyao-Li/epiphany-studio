from __future__ import annotations

from typing import Any

from epiphany.interview_schemas import InterviewScaffoldOutput
from epiphany.schemas import SourceReference


def _source_label(reference: SourceReference) -> str:
    return f"`{reference.source_id}#{reference.source_segment_id}`"


def _source_line(references: list[SourceReference]) -> str:
    return "来源：" + "、".join(_source_label(reference) for reference in references)


def render_interview_scaffold_markdown(content: dict[str, Any]) -> str:
    scaffold = InterviewScaffoldOutput.model_validate(content)
    lines = [
        f"# {scaffold.title}",
        "",
        f"> {scaffold.episode_intent}",
        "",
        "## 开场",
        "",
        scaffold.opening,
        "",
    ]

    for index, section in enumerate(scaffold.sections, start=1):
        lines.extend(
            [
                f"## {index}. {section.title}",
                "",
                "### 已知背景",
                "",
            ]
        )
        for statement in section.known_context:
            lines.extend(
                [
                    f"- {statement.text}",
                    f"  - {_source_line(statement.source_refs)}",
                ]
            )

        lines.extend(
            [
                "",
                "### 可直接说的过渡",
                "",
                f"> {section.transition.text}",
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
                    f"{question_index}. {question.prompt}",
                    f"   - 追问目的：{question.purpose}",
                    f"   - 关键词：{' / '.join(question.keywords)}",
                    f"   - {_source_line(question.source_refs)}",
                ]
            )
        lines.append("")

    if scaffold.material_gaps:
        lines.extend(["## 还缺少的素材", ""])
        for gap in scaffold.material_gaps:
            lines.extend(
                [
                    f"- {gap.gap}",
                    f"  - 为什么值得补充：{gap.why_it_matters}",
                    f"  - {_source_line(gap.source_refs)}",
                ]
            )
        lines.append("")

    lines.extend(["## 收束", "", scaffold.closing, ""])
    return "\n".join(lines)
