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
    MAX_LENGTH_RECOVERY_PRIORITY_REFS,
    DraftImprovementPlan,
    build_draft_length_recovery_plan,
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


def _script_character_count(draft: PodcastDraftOutput) -> int:
    texts = [
        draft.podcast_script.opening.text,
        *[
            paragraph.text
            for section in draft.podcast_script.sections
            for paragraph in section.paragraphs
        ],
        draft.podcast_script.closing.text,
    ]
    return sum(len("".join(text.split())) for text in texts)


def _build(
    *,
    target_minutes: int = 10,
    unused_text: str | None,
    with_material_gap: bool = False,
    selected_feedback_codes: tuple[str, ...] = (),
    writing_style_context_available: bool = False,
    prior_length_recovery_attempted: bool = False,
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
        prior_length_recovery_attempted=prior_length_recovery_attempted,
        selected_feedback_codes=selected_feedback_codes,
    )


def _build_from_input(
    *,
    editor_input: dict[str, object],
    draft: PodcastDraftOutput,
) -> DraftImprovementPlan:
    return build_draft_improvement_plan(
        parent_run_id="run_parent",
        parent_draft_artifact_id="art_draft",
        quality_report_artifact_id="art_quality",
        editor_task_input=editor_input,
        podcast_draft=draft,
        quality_report=_report(draft, editor_input),
        interview_scaffold=editor_input["interview_scaffold"],
        writing_style_context_available=False,
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
    assert "一次受控扩写尝试" in options["reuse_unused_material"].explanation
    assert "不保证" in options["reuse_unused_material"].explanation
    assert "add_supplemental_material" not in options
    assert "lower_target_duration" not in options
    assert secret_source_text not in plan.model_dump_json()


def test_show_notes_only_reference_remains_unused_for_spoken_recovery() -> None:
    """Show Notes metadata must not consume material needed by the spoken script."""

    draft_content = _draft().model_dump(mode="json")
    draft_content["show_notes"]["key_points"].append(
        _grounded(
            "这个事实目前只出现在节目简介里，还没有进入口播正文。",
            UNUSED_REF,
        )
    )
    draft = PodcastDraftOutput.model_validate(draft_content)

    plan = _build(
        unused_text=("重听录音时，她先听见雨点落在空调外机上，随后停顿了三秒。" * 120),
        draft=draft,
    )

    assert plan.material.unused_factual_segment_count == 1
    assert [reference.model_dump() for reference in plan.material.unused_source_refs] == [
        UNUSED_REF
    ]
    assert plan.duration_resolution == "reuse_unused_material"


def test_priority_candidates_are_bounded_and_missing_must_include_ranks_first() -> None:
    draft = _draft()
    editor_input = _editor_input(
        target_minutes=10,
        unused_text=None,
    )
    editor_input["creative_brief"]["must_include"] = ["钥匙装在透明文件袋里"]
    extra_segments = [
        {
            "source_id": f"src_extra_{index:02d}",
            "source_segment_id": f"seg_extra_{index:02d}",
            "text": f"搬家后的第{index + 1}个普通片段，有一个可继续展开的生活细节。",
        }
        for index in range(MAX_LENGTH_RECOVERY_PRIORITY_REFS + 4)
    ]
    must_include_ref = {
        "source_id": "src_must_include",
        "source_segment_id": "seg_must_include",
    }
    extra_segments.append(
        {
            **must_include_ref,
            "text": "交房那天，她把钥匙装在透明文件袋里，又在袋口贴了一张黄色便签。",
        }
    )
    editor_input["initial_source_segments"].extend(extra_segments)

    plan = _build_from_input(editor_input=editor_input, draft=draft)

    candidates = plan.material.priority_candidate_source_refs
    assert plan.material.priority_candidates_assessed is True
    assert len(candidates) == MAX_LENGTH_RECOVERY_PRIORITY_REFS
    assert candidates[0].model_dump() == must_include_ref
    candidate_keys = {
        (reference.source_id, reference.source_segment_id) for reference in candidates
    }
    source_by_key = {
        (segment["source_id"], segment["source_segment_id"]): segment["text"]
        for segment in editor_input["initial_source_segments"]
    }
    assert plan.material.priority_candidate_character_count == sum(
        len("".join(source_by_key[key].split())) for key in candidate_keys
    )

    recovery = build_draft_length_recovery_plan(
        improvement_plan=plan,
        target_duration_minutes=10,
    )
    assert len(recovery.priority_unused_source_refs) == MAX_LENGTH_RECOVERY_PRIORITY_REFS
    assert recovery.available_unused_character_count == (
        plan.material.priority_candidate_character_count
    )


def test_priority_candidates_skip_exact_spoken_copy_and_duplicate_source_text() -> None:
    draft = _draft()
    editor_input = _editor_input(
        target_minutes=10,
        unused_text=None,
    )
    copied_ref = {
        "source_id": "src_copied",
        "source_segment_id": "seg_copied",
    }
    first_duplicate_ref = {
        "source_id": "src_duplicate_a",
        "source_segment_id": "seg_duplicate_a",
    }
    second_duplicate_ref = {
        "source_id": "src_duplicate_b",
        "source_segment_id": "seg_duplicate_b",
    }
    distinct_ref = {
        "source_id": "src_distinct",
        "source_segment_id": "seg_distinct",
    }
    duplicate_text = "搬空房间以后，她第一次听见行李箱轮子在地板上来回滚动的声音。"
    editor_input["initial_source_segments"].extend(
        [
            {
                **copied_ref,
                "text": draft.podcast_script.opening.text,
            },
            {
                **first_duplicate_ref,
                "text": duplicate_text,
            },
            {
                **second_duplicate_ref,
                "text": duplicate_text,
            },
            {
                **distinct_ref,
                "text": "第二天早上，她仍然下意识走向旧住址楼下的早餐店。",
            },
        ]
    )

    plan = _build_from_input(editor_input=editor_input, draft=draft)

    # The full inventory stays auditable; only the safe shortlist is filtered.
    assert plan.material.unused_factual_segment_count == 4
    candidate_keys = [
        (reference.source_id, reference.source_segment_id)
        for reference in plan.material.priority_candidate_source_refs
    ]
    assert (copied_ref["source_id"], copied_ref["source_segment_id"]) not in candidate_keys
    assert (
        first_duplicate_ref["source_id"],
        first_duplicate_ref["source_segment_id"],
    ) in candidate_keys
    assert (
        second_duplicate_ref["source_id"],
        second_duplicate_ref["source_segment_id"],
    ) not in candidate_keys
    assert (distinct_ref["source_id"], distinct_ref["source_segment_id"]) in candidate_keys


def test_raw_unused_volume_does_not_override_an_empty_candidate_shortlist() -> None:
    draft = _draft(extra_text="甲" * 1_000)
    editor_input = _editor_input(
        target_minutes=15,
        unused_text=None,
    )
    copied_text = draft.podcast_script.opening.text
    editor_input["initial_source_segments"].extend(
        [
            {
                "source_id": f"src_copied_{index}",
                "source_segment_id": f"seg_copied_{index}",
                "text": copied_text,
            }
            for index in range(4)
        ]
    )

    plan = _build_from_input(editor_input=editor_input, draft=draft)

    assert (
        plan.material.unused_factual_character_count >= plan.duration.missing_script_character_count
    )
    assert plan.material.priority_candidates_assessed is True
    assert plan.material.priority_candidate_character_count == 0
    assert plan.material.priority_candidate_source_refs == []
    assert plan.duration_resolution == "add_supplemental_material"
    assert "reuse_unused_material" not in {option.kind for option in plan.options}


def test_duration_inside_tolerance_does_not_require_center_target_recovery() -> None:
    target_characters = 2_800
    minimum_characters = 2_380
    desired_characters = 2_500
    base = _draft()
    draft = _draft(
        extra_text="甲" * (desired_characters - _script_character_count(base)),
    )

    plan = _build(
        target_minutes=10,
        unused_text="这段素材虽然还没使用，但合格稿不应为了追中心值而被迫扩写。" * 30,
        draft=draft,
    )

    assert plan.duration.actual_script_character_count == desired_characters
    assert minimum_characters <= desired_characters < target_characters
    assert plan.duration.missing_script_character_count == (target_characters - desired_characters)
    assert plan.duration_resolution == "not_needed"
    assert "reuse_unused_material" not in {
        option.kind for option in plan.options if option.recommended
    }


def test_pre_m38_v1_plan_inside_tolerance_remains_readable() -> None:
    target_characters = 2_800
    desired_characters = 2_500
    base = _draft()
    draft = _draft(
        extra_text="甲" * (desired_characters - _script_character_count(base)),
    )
    current = _build(
        target_minutes=10,
        unused_text="旧版本会把距离中心目标仍有缺口视为需要继续复用素材。" * 30,
        draft=draft,
    )
    legacy = current.model_dump(mode="json")
    legacy["schema_version"] = "draft_improvement_plan_v1"
    legacy.pop("prior_length_recovery_attempted")
    legacy["material"].pop("priority_candidates_assessed")
    legacy["material"].pop("priority_candidate_character_count")
    legacy["material"].pop("priority_candidate_source_refs")
    legacy["duration_resolution"] = "reuse_unused_material"
    legacy["options"].insert(
        0,
        {
            "kind": "reuse_unused_material",
            "recommended": True,
            "explanation": "这是升级前已经持久化的 v1 计划。",
            "source_refs": legacy["material"]["unused_source_refs"],
            "suggested_target_duration_minutes": None,
        },
    )

    parsed = DraftImprovementPlan.model_validate(legacy)

    assert parsed.schema_version == "draft_improvement_plan_v1"
    assert parsed.duration.actual_script_character_count == desired_characters
    assert parsed.duration.actual_script_character_count < target_characters
    assert parsed.duration_resolution == "reuse_unused_material"
    assert parsed.material.priority_candidates_assessed is False
    assert parsed.prior_length_recovery_attempted is False

    recovery = build_draft_length_recovery_plan(
        improvement_plan=parsed,
        target_duration_minutes=10,
    )
    assert recovery.available_unused_character_count == (
        parsed.material.unused_factual_character_count
    )
    assert recovery.priority_unused_source_refs == parsed.material.unused_source_refs


def test_unused_material_that_reaches_minimum_is_sufficient_for_recovery() -> None:
    target_characters = 2_800
    minimum_characters = 2_380
    desired_characters = 2_200
    base = _draft()
    draft = _draft(
        extra_text="甲" * (desired_characters - _script_character_count(base)),
    )
    missing_to_minimum = minimum_characters - desired_characters
    missing_to_target = target_characters - desired_characters
    available_unused_characters = (missing_to_minimum + missing_to_target) // 2

    plan = _build(
        target_minutes=10,
        unused_text="乙" * available_unused_characters,
        draft=draft,
    )

    assert 0 < missing_to_minimum <= plan.material.unused_factual_character_count
    assert plan.material.unused_factual_character_count < missing_to_target
    assert plan.duration_resolution == "reuse_unused_material"
    reuse = next(option for option in plan.options if option.kind == "reuse_unused_material")
    assert reuse.recommended is True
    assert all(option.kind != "add_supplemental_material" for option in plan.options)


def test_insufficient_unused_material_recommends_supplement_and_lower_preset() -> None:
    plan = _build(
        target_minutes=15,
        unused_text="很短的未使用材料。",
    )

    assert plan.duration_resolution == "reuse_then_supplement"
    options = {option.kind: option for option in plan.options}
    assert options["reuse_unused_material"].recommended is False
    assert "仍低于当前时长缺口" in options["reuse_unused_material"].explanation
    assert "补充材料或降低目标时长" in options["reuse_unused_material"].explanation
    assert options["add_supplemental_material"].recommended is True
    assert options["lower_target_duration"].recommended is False
    assert options["lower_target_duration"].suggested_target_duration_minutes == 10


def test_prior_length_recovery_attempt_stops_recommending_consecutive_expansion() -> None:
    plan = _build(
        target_minutes=15,
        unused_text="仍然存在但不应被连续自动扩写的具体材料。" * 300,
        prior_length_recovery_attempted=True,
    )

    assert plan.schema_version == DRAFT_IMPROVEMENT_PLAN_VERSION
    assert plan.prior_length_recovery_attempted is True
    assert plan.duration_resolution == "add_supplemental_material"
    options = {option.kind: option for option in plan.options}
    assert options["reuse_unused_material"].recommended is False
    assert "不再推荐连续扩写" in options["reuse_unused_material"].explanation
    assert options["add_supplemental_material"].recommended is True
    assert "一次有来源的恢复后仍未达到最低时长" in (
        options["add_supplemental_material"].explanation
    )
    assert options["lower_target_duration"].recommended is True


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
