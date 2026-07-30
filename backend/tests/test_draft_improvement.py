from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from epiphany.draft_improvement import (
    DraftImprovementPlanInputError,
    build_draft_improvement_plan,
)
from epiphany.draft_quality import (
    analyze_podcast_draft,
    build_draft_quality_report,
)
from epiphany.draft_quality_schemas import DraftQualityReport
from epiphany.editor_schemas import PodcastDraftOutput
from epiphany.interview_schemas import InterviewScaffoldOutput
from epiphany.revision_schemas import (
    DRAFT_IMPROVEMENT_PLAN_VERSION,
    DraftImprovementPlan,
)

INITIAL_REF = {
    "source_id": "src_initial",
    "source_segment_id": "seg_initial",
}
SUPPLEMENTAL_REF = {
    "source_id": "src_supplemental",
    "source_segment_id": "seg_supplemental",
}
UNUSED_REF = {
    "source_id": "src_unused",
    "source_segment_id": "seg_unused",
}


def _grounded(
    text: str,
    reference: dict[str, str] = INITIAL_REF,
) -> dict[str, object]:
    return {"text": text, "source_refs": [reference]}


def _scaffold(*, with_material_gap: bool = False) -> InterviewScaffoldOutput:
    material_gaps = (
        [
            {
                "gap": "还缺少重新听见旧录音时的身体感受。",
                "why_it_matters": "具体感受能让开场不只停留在抽象总结。",
                "source_refs": [INITIAL_REF],
            }
        ]
        if with_material_gap
        else []
    )
    return InterviewScaffoldOutput.model_validate(
        {
            "title": "五年后重新打开播客",
            "episode_intent": _grounded("解释为什么决定重新开始记录。"),
            "opening": _grounded("先回到重新听见旧声音的时刻。"),
            "sections": [
                {
                    "title": "旧录音",
                    "source_refs": [INITIAL_REF],
                    "known_context": [_grounded("五年前留下过一段录音。")],
                    "transition": _grounded("先说重新按下播放键。"),
                    "questions": [
                        {
                            "prompt": "第一次重听旧录音时，你最先注意到什么？",
                            "purpose": "补充可被听见的具体细节。",
                            "keywords": ["声音", "停顿"],
                            "source_refs": [INITIAL_REF],
                        }
                    ],
                },
                {
                    "title": "重新开始",
                    "source_refs": [INITIAL_REF],
                    "known_context": [_grounded("播客停更了五年。")],
                    "transition": _grounded("再说为什么偏偏是现在。"),
                    "questions": [
                        {
                            "prompt": "为什么这一次不再等完全准备好？",
                            "purpose": "补充重新开始的决定过程。",
                            "keywords": ["完美主义", "行动"],
                            "source_refs": [INITIAL_REF],
                        }
                    ],
                },
            ],
            "material_gaps": material_gaps,
            "closing": _grounded("把这次重新开始留给未来的自己。"),
        }
    )


def _draft(*, extra_text: str = "") -> PodcastDraftOutput:
    return PodcastDraftOutput.model_validate(
        {
            "title": "五年后重新打开播客",
            "podcast_script": {
                "opening": _grounded("前几天，我重新听见了五年前的声音。" + extra_text),
                "sections": [
                    {
                        "title": "旧录音",
                        "source_refs": [INITIAL_REF],
                        "paragraphs": [
                            _grounded("声音里留着当时的紧张和停顿。"),
                        ],
                    },
                    {
                        "title": "重新开始",
                        "source_refs": [SUPPLEMENTAL_REF],
                        "paragraphs": [
                            _grounded(
                                "这一次我决定先完成，再慢慢修改。",
                                SUPPLEMENTAL_REF,
                            )
                        ],
                    },
                ],
                "closing": _grounded(
                    "我想继续给未来留下声音。",
                    SUPPLEMENTAL_REF,
                ),
            },
            "show_notes": {
                "summary": _grounded(
                    "重听旧录音后决定重新开始。",
                    SUPPLEMENTAL_REF,
                ),
                "key_points": [
                    _grounded("声音会保留停顿。"),
                    _grounded("先完成再修改。", SUPPLEMENTAL_REF),
                ],
            },
        }
    )


def _editor_input(
    *,
    target_minutes: int = 10,
    unused_text: str | None,
    with_material_gap: bool = False,
) -> dict[str, object]:
    initial_segments = [
        {
            **INITIAL_REF,
            "text": "五年前留下过旧录音，后来重新打开时听见了当时的停顿。",
        }
    ]
    if unused_text is not None:
        initial_segments.append({**UNUSED_REF, "text": unused_text})
    scaffold = _scaffold(with_material_gap=with_material_gap)
    return {
        "task_kind": "build_podcast_draft",
        "topic": "五年后重新打开播客",
        "scaffold_artifact_id": "art_scaffold",
        "submission_artifact_id": "art_submission",
        "creative_brief": {
            "target_duration_minutes": target_minutes,
            "speaking_rate_chars_per_minute": 280,
            "scenario": "reflective_solo",
            "target_audience": "正在犹豫要不要开始创作的人",
            "communication_goal": "用具体经历解释重新开始的原因",
            "tone": ["自然", "克制"],
            "must_include": [],
            "avoid_patterns": [],
        },
        "interview_scaffold": scaffold.model_dump(mode="json"),
        "initial_source_segments": initial_segments,
        "supplemental_source_segments": [
            {
                **SUPPLEMENTAL_REF,
                "text": "补充口述说明这次决定先完成一版，再根据真实录音修改。",
            }
        ],
    }


def _report(
    draft: PodcastDraftOutput,
    editor_input: dict[str, object],
) -> DraftQualityReport:
    deterministic = analyze_podcast_draft(
        draft=draft,
        creative_brief=editor_input["creative_brief"],
    )
    return build_draft_quality_report(
        deterministic=deterministic,
        model_self_review=None,
        unavailable_reason="unit_test",
    )


def _build(
    *,
    target_minutes: int = 10,
    unused_text: str | None,
    with_material_gap: bool = False,
    selected_feedback_codes: tuple[str, ...] = (),
    writing_style_context_available: bool = False,
    draft: PodcastDraftOutput | None = None,
) -> DraftImprovementPlan:
    selected_draft = draft or _draft()
    editor_input = _editor_input(
        target_minutes=target_minutes,
        unused_text=unused_text,
        with_material_gap=with_material_gap,
    )
    return build_draft_improvement_plan(
        parent_run_id="run_parent",
        parent_draft_artifact_id="art_draft",
        quality_report_artifact_id="art_quality",
        editor_task_input=editor_input,
        podcast_draft=selected_draft,
        quality_report=_report(selected_draft, editor_input),
        interview_scaffold=editor_input["interview_scaffold"],
        writing_style_context_available=writing_style_context_available,
        selected_feedback_codes=selected_feedback_codes,
    )


def test_plan_exactly_accounts_for_duration_and_recommends_unused_material() -> None:
    secret_source_text = "只应参与计数绝不能复制进计划" * 400
    plan = _build(unused_text=secret_source_text)

    assert plan.schema_version == DRAFT_IMPROVEMENT_PLAN_VERSION
    assert plan.duration.target_script_character_count == 2_800
    assert (
        plan.duration.missing_script_character_count
        == plan.duration.target_script_character_count - plan.duration.actual_script_character_count
    )
    assert plan.duration.missing_duration_minutes == round(
        plan.duration.missing_script_character_count / 280,
        2,
    )
    assert plan.material.unused_factual_segment_count == 1
    assert plan.material.unused_factual_character_count == len(secret_source_text)
    assert plan.material.unused_source_refs[0].model_dump() == UNUSED_REF
    assert plan.duration_resolution == "reuse_unused_material"

    options = {option.kind: option for option in plan.options}
    assert options["reuse_unused_material"].recommended is True
    assert "add_supplemental_material" not in options
    assert "lower_target_duration" not in options
    assert secret_source_text not in plan.model_dump_json()


def test_insufficient_unused_material_recommends_supplement_and_lower_preset() -> None:
    plan = _build(
        target_minutes=15,
        unused_text="很短的未使用材料。",
    )

    assert plan.duration_resolution == "reuse_then_supplement"
    options = {option.kind: option for option in plan.options}
    assert options["reuse_unused_material"].recommended is False
    assert options["add_supplemental_material"].recommended is True
    assert options["lower_target_duration"].recommended is False
    assert options["lower_target_duration"].suggested_target_duration_minutes == 10


def test_no_unused_material_only_recommends_targeted_supplement() -> None:
    plan = _build(unused_text=None)

    assert plan.duration_resolution == "add_supplemental_material"
    option_kinds = [option.kind for option in plan.options]
    assert option_kinds == ["add_supplemental_material"]


def test_questions_are_three_to_six_and_trace_back_to_scaffold_only() -> None:
    plan = _build(
        unused_text="短材料",
        with_material_gap=True,
    )

    assert 3 <= len(plan.targeted_questions) <= 6
    first = plan.targeted_questions[0]
    assert first.anchor_kind == "material_gap"
    assert first.anchor_path == "material_gaps[0]"
    assert first.anchor_text == "还缺少重新听见旧录音时的身体感受。"
    assert "还缺少重新听见旧录音时的身体感受" in first.prompt
    assert all(
        question.anchor_kind in {"material_gap", "scaffold_question"}
        for question in plan.targeted_questions
    )
    assert any(
        gap.code == "scaffold.material_gap.0"
        and gap.explanation.startswith("还缺少重新听见旧录音时的身体感受")
        for gap in plan.gaps
    )


def test_selected_feedback_and_style_context_are_explicit_trace_not_prose() -> None:
    plan = _build(
        unused_text=None,
        writing_style_context_available=True,
        selected_feedback_codes=("voice.too_formal", "opening.too_generic"),
    )

    assert plan.writing_style_context_available is True
    assert plan.selected_feedback_codes == [
        "voice.too_formal",
        "opening.too_generic",
    ]
    option = next(option for option in plan.options if option.kind == "apply_selected_feedback")
    assert option.recommended is True
    assert {gap.code for gap in plan.gaps if gap.kind == "selected_feedback"} == {
        "selected_feedback.voice.too_formal",
        "selected_feedback.opening.too_generic",
    }


def test_builder_rejects_mismatched_report_scaffold_and_duplicate_feedback() -> None:
    draft = _draft()
    editor_input = _editor_input(
        target_minutes=10,
        unused_text="未使用材料",
    )
    other_draft = _draft(extra_text="另一个版本" * 20)
    report = _report(other_draft, editor_input)

    with pytest.raises(DraftImprovementPlanInputError):
        build_draft_improvement_plan(
            parent_run_id="run_parent",
            parent_draft_artifact_id="art_draft",
            quality_report_artifact_id="art_quality",
            editor_task_input=editor_input,
            podcast_draft=draft,
            quality_report=report,
            interview_scaffold=editor_input["interview_scaffold"],
            writing_style_context_available=False,
        )

    mismatched_scaffold = deepcopy(editor_input["interview_scaffold"])
    mismatched_scaffold["closing"]["text"] = "另一个收束。"
    with pytest.raises(DraftImprovementPlanInputError):
        build_draft_improvement_plan(
            parent_run_id="run_parent",
            parent_draft_artifact_id="art_draft",
            quality_report_artifact_id="art_quality",
            editor_task_input=editor_input,
            podcast_draft=draft,
            quality_report=_report(draft, editor_input),
            interview_scaffold=mismatched_scaffold,
            writing_style_context_available=False,
        )

    with pytest.raises(DraftImprovementPlanInputError):
        build_draft_improvement_plan(
            parent_run_id="run_parent",
            parent_draft_artifact_id="art_draft",
            quality_report_artifact_id="art_quality",
            editor_task_input=editor_input,
            podcast_draft=draft,
            quality_report=_report(draft, editor_input),
            interview_scaffold=editor_input["interview_scaffold"],
            writing_style_context_available=False,
            selected_feedback_codes=("same", "same"),
        )


def test_plan_schema_forbids_unknown_fields_and_inconsistent_options() -> None:
    plan = _build(unused_text=None)
    content = plan.model_dump(mode="python")
    content["unexpected"] = True
    with pytest.raises(ValidationError):
        DraftImprovementPlan.model_validate(content)

    inconsistent = plan.model_dump(mode="python")
    inconsistent["selected_feedback_codes"] = ["voice.too_formal"]
    with pytest.raises(ValidationError):
        DraftImprovementPlan.model_validate(inconsistent)
