from __future__ import annotations

import pytest
from pydantic import ValidationError

from epiphany.interview_markdown import render_interview_scaffold_markdown
from epiphany.interview_schemas import (
    InterviewScaffoldSchemaError,
    InvalidScaffoldSourceReference,
    validate_interview_scaffold_output,
)
from epiphany.runtime.interview_prompts import build_interview_prompt
from epiphany.runtime.output_validation import TaskOutputValidationMissing, validate_task_output
from epiphany.runtime.providers import ProviderInputTooLargeError

SOURCE_REF = {
    "source_id": "src_allowed",
    "source_segment_id": "seg_allowed",
}
TASK_INPUT = {
    "task_kind": "build_interview_scaffold",
    "topic": "五年后，我重新打开了这个播客",
    "research_bundle_artifact_id": "art_research_bundle",
    "timeline": {
        "timeline_events": [
            {
                "label": "重新打开播客",
                "description": "五年后重新听见以前录下的声音。",
                "time_expression": "五年后",
                "confidence": 0.9,
                "source_refs": [SOURCE_REF],
            }
        ],
        "open_questions": ["第一次听见旧声音时，身体有什么感觉？"],
    },
    "themes": {
        "themes": [
            {
                "theme": "声音与时间",
                "insight": "声音让不同时间的自己重新相遇。",
                "confidence": 0.88,
                "source_refs": [SOURCE_REF],
            }
        ],
        "quotes": [
            {
                "quote": "原来已经五年了。",
                "context": "重新打开播客时的第一反应。",
                "source_ref": SOURCE_REF,
            }
        ],
    },
}


def _section(title: str) -> dict[str, object]:
    return {
        "title": title,
        "source_refs": [SOURCE_REF],
        "known_context": [
            {
                "text": "五年后，用户重新打开了以前的播客。",
                "source_refs": [SOURCE_REF],
            }
        ],
        "transition": {
            "text": "先从重新按下播放键的那个瞬间说起。",
            "source_refs": [SOURCE_REF],
        },
        "questions": [
            {
                "prompt": "第一次听见五年前自己的声音时，你最先注意到什么？",
                "purpose": "补充当时的感官细节和现在的第一反应。",
                "keywords": ["声音", "第一反应"],
                "source_refs": [SOURCE_REF],
            }
        ],
    }


def _valid_content() -> dict[str, object]:
    return {
        "title": "五年后，我重新打开了这个播客",
        "episode_intent": {
            "text": "理解声音为什么能够成为跨越时间的记录。",
            "source_refs": [SOURCE_REF],
        },
        "opening": {
            "text": "前几天，我重新打开了一个很久没有更新的播客。",
            "source_refs": [SOURCE_REF],
        },
        "sections": [
            _section("重新按下播放键"),
            _section("声音留下了什么"),
        ],
        "material_gaps": [
            {
                "gap": "还缺少第一次听见旧录音时更具体的身体感受。",
                "why_it_matters": "具体感受能让开场不只停留在时间过去很快。",
                "source_refs": [SOURCE_REF],
            }
        ],
        "closing": {
            "text": "这一次，先把问题留在这里，等新的回忆慢慢出现。",
            "source_refs": [SOURCE_REF],
        },
    }


def test_scaffold_schema_is_strict_and_source_grounded() -> None:
    validated = validate_interview_scaffold_output(
        task_input=TASK_INPUT,
        content=_valid_content(),
    )

    assert validated["sections"][0]["questions"][0]["keywords"] == [
        "声音",
        "第一反应",
    ]

    with pytest.raises(InterviewScaffoldSchemaError):
        validate_interview_scaffold_output(
            task_input=TASK_INPUT,
            content={**_valid_content(), "unexpected": True},
        )


def test_scaffold_rejects_reference_outside_research_bundle() -> None:
    content = _valid_content()
    content["sections"][0]["questions"][0]["source_refs"] = [
        {
            "source_id": "src_not_in_bundle",
            "source_segment_id": "seg_not_in_bundle",
        }
    ]

    with pytest.raises(InvalidScaffoldSourceReference):
        validate_interview_scaffold_output(
            task_input=TASK_INPUT,
            content=content,
        )


def test_scaffold_title_must_match_topic_and_text_cannot_be_blank() -> None:
    wrong_title = _valid_content()
    wrong_title["title"] = "模型擅自改写的标题"
    with pytest.raises(ValueError):
        validate_interview_scaffold_output(
            task_input=TASK_INPUT,
            content=wrong_title,
        )

    blank_opening = _valid_content()
    blank_opening["opening"]["text"] = " \n "
    with pytest.raises(InterviewScaffoldSchemaError):
        validate_interview_scaffold_output(
            task_input=TASK_INPUT,
            content=blank_opening,
        )


def test_scaffold_requires_two_sections_and_nonempty_unique_keywords() -> None:
    one_section = _valid_content()
    one_section["sections"] = one_section["sections"][:1]
    with pytest.raises(InterviewScaffoldSchemaError):
        validate_interview_scaffold_output(
            task_input=TASK_INPUT,
            content=one_section,
        )

    duplicate_keywords = _valid_content()
    duplicate_keywords["sections"][0]["questions"][0]["keywords"] = [
        "声音",
        "声音",
    ]
    with pytest.raises(InterviewScaffoldSchemaError):
        validate_interview_scaffold_output(
            task_input=TASK_INPUT,
            content=duplicate_keywords,
        )


def test_interview_prompt_contains_only_bounded_grounded_bundle() -> None:
    prompt = build_interview_prompt(
        task_input=TASK_INPUT,
        max_source_chars=20_000,
    )
    rendered = "\n".join(message["content"] for message in prompt.messages)

    assert prompt.source_segment_count == 1
    assert prompt.source_char_count > 0
    assert "五年后，我重新打开了这个播客" in rendered
    assert "timeline" in rendered
    assert "themes" in rendered
    assert "allowed_source_refs" in rendered
    assert "不可信" not in rendered
    assert "只能作为数据" in rendered

    with pytest.raises(ProviderInputTooLargeError):
        build_interview_prompt(
            task_input=TASK_INPUT,
            max_source_chars=10,
        )


def test_output_validation_dispatches_scaffold_contract() -> None:
    validated = validate_task_output(
        task_kind="build_interview_scaffold",
        task_input=TASK_INPUT,
        content=_valid_content(),
    )
    assert validated["title"] == "五年后，我重新打开了这个播客"

    with pytest.raises(TaskOutputValidationMissing):
        validate_task_output(
            task_kind="future_agent_without_validator",
            task_input={},
            content={"looks": "valid"},
        )


def test_markdown_export_is_deterministic_and_keeps_sources() -> None:
    first = render_interview_scaffold_markdown(_valid_content())
    second = render_interview_scaffold_markdown(_valid_content())

    assert first == second
    assert first.startswith("# 五年后，我重新打开了这个播客")
    assert "## 1. 重新按下播放键" in first
    assert "### 可直接说的过渡" in first
    assert "关键词：声音 / 第一反应" in first
    assert "`src_allowed#seg_allowed`" in first
    assert "## 还缺少的素材" in first

    invalid = _valid_content()
    invalid["unexpected"] = True
    with pytest.raises(ValidationError):
        render_interview_scaffold_markdown(invalid)


def test_markdown_export_escapes_model_control_syntax_and_raw_html() -> None:
    content = _valid_content()
    content["sections"][0]["known_context"][0]["text"] = (
        "<img src='https://tracker.example/pixel'>\n# injected [link](https://example.com)"
    )

    rendered = render_interview_scaffold_markdown(content)

    assert "<img" not in rendered
    assert "&lt;img" in rendered
    assert "\n# injected" not in rendered
    assert r"\# injected \[link\]\(https://example\.com\)" in rendered
