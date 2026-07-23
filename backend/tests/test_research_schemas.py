from __future__ import annotations

import pytest

from epiphany.research_schemas import (
    InvalidSourceReference,
    QuoteSourceMismatch,
    ResearchSchemaError,
    validate_research_output,
)

TASK_INPUT = {
    "source_segments": [
        {
            "source_id": "src_allowed",
            "source_segment_id": "seg_allowed",
            "text": "2019年第一次记录项目，2024年重新整理旧笔记。",
        }
    ]
}
SOURCE_REF = {
    "source_id": "src_allowed",
    "source_segment_id": "seg_allowed",
}


def test_timeline_output_is_strict_and_source_grounded() -> None:
    content = {
        "timeline_events": [
            {
                "label": "重新打开播客",
                "description": "一次跨越五年的项目回望。",
                "time_expression": "五年后",
                "confidence": 0.9,
                "source_refs": [SOURCE_REF],
            }
        ],
        "open_questions": [],
    }

    assert (
        validate_research_output(
            task_kind="timeline_research",
            task_input=TASK_INPUT,
            content=content,
        )["timeline_events"][0]["label"]
        == "重新打开播客"
    )

    with pytest.raises(ResearchSchemaError):
        validate_research_output(
            task_kind="timeline_research",
            task_input=TASK_INPUT,
            content={**content, "unexpected": True},
        )


def test_reference_outside_task_scope_is_rejected() -> None:
    with pytest.raises(InvalidSourceReference):
        validate_research_output(
            task_kind="timeline_research",
            task_input=TASK_INPUT,
            content={
                "timeline_events": [
                    {
                        "label": "Unsupported",
                        "description": "This reference was not provided to the task.",
                        "confidence": 0.4,
                        "source_refs": [
                            {
                                "source_id": "src_other",
                                "source_segment_id": "seg_other",
                            }
                        ],
                    }
                ],
                "open_questions": [],
            },
        )


def test_quote_must_exist_verbatim_in_referenced_segment() -> None:
    with pytest.raises(QuoteSourceMismatch):
        validate_research_output(
            task_kind="theme_research",
            task_input=TASK_INPUT,
            content={
                "themes": [
                    {
                        "theme": "时间",
                        "insight": "声音保存了当时的自己。",
                        "confidence": 0.8,
                        "source_refs": [SOURCE_REF],
                    }
                ],
                "quotes": [
                    {
                        "quote": "这句话并不存在于原文中。",
                        "context": None,
                        "source_ref": SOURCE_REF,
                    }
                ],
            },
        )
