from __future__ import annotations

from epiphany.revision_schemas import (
    DraftLengthRecoveryPlan,
    PodcastRevisionTaskInput,
)
from epiphany.runtime.revision_prompts import (
    _REVISION_INSTRUCTIONS,
    _length_recovery_instructions,
    _repair_rule_instruction,
)


def _recovery_task(
    *,
    readiness: str = "existing_material_sufficient",
) -> PodcastRevisionTaskInput:
    priority_refs = [
        {
            "source_id": f"src_{index}",
            "source_segment_id": f"seg_{index}",
        }
        for index in range(22)
    ]
    plan = DraftLengthRecoveryPlan(
        actual_script_character_count=1817,
        minimum_script_character_count=3570,
        target_script_character_count=4200,
        maximum_script_character_count=4830,
        missing_to_minimum_character_count=1753,
        missing_to_target_character_count=2383,
        available_unused_character_count=2789,
        readiness=readiness,
        priority_unused_source_refs=priority_refs,
    )
    # This unit only exercises prompt projection. Full task-contract validation
    # remains covered by revision workflow tests.
    return PodcastRevisionTaskInput.model_construct(
        selected_actions=["reuse_unused_material"],
        length_recovery_plan=plan,
    )


def test_sufficient_length_recovery_prompt_forbids_no_change_and_requires_new_evidence() -> None:
    instructions = _length_recovery_instructions(_recovery_task())

    assert "父稿口播正文为 1817" in instructions
    assert "可接受下限为 3570" in instructions
    assert "编辑目标为 4200" in instructions
    assert "上限为 4830" in instructions
    assert "距可接受下限还差 1753" in instructions
    assert "有 22 个候选" in instructions
    assert "合计约 2789 个来源字符" in instructions
    assert "禁止原样返回父稿" in instructions
    assert "至少 2 个" in instructions
    assert "至少要让 1 个父稿口播从未使用的" in instructions
    assert "opening、paragraph 或 closing" in instructions
    assert "section metadata 或 Show Notes 不算使用" in instructions
    assert "可以扩写已有段落，也可以增加一个有叙事作用的 section" in instructions
    assert "最大化信息增量版本" in instructions
    assert "不得以素材可能不足为理由原样返回父稿" in instructions


def test_revision_prompt_forbids_editorial_notes_from_leaking_into_spoken_text() -> None:
    assert "编辑备注" in _REVISION_INSTRUCTIONS
    assert "需要在正文" in _REVISION_INSTRUCTIONS
    assert "如果要用" in _REVISION_INSTRUCTIONS
    assert "不得出现" in _REVISION_INSTRUCTIONS


def test_revision_prompt_repairs_semantic_duplicates_and_source_conflicts() -> None:
    assert "按事件比较父稿" in _REVISION_INSTRUCTIONS
    assert "概要版和详细版" in _REVISION_INSTRUCTIONS
    assert "即使字面没有完全重复" in _REVISION_INSTRUCTIONS
    assert "也要合并成一次" in _REVISION_INSTRUCTIONS
    assert "互斥事实" in _REVISION_INSTRUCTIONS
    assert "采用该较新补充" in _REVISION_INSTRUCTIONS
    assert "无法判断时避免" in _REVISION_INSTRUCTIONS


def test_length_recovery_prompt_is_absent_without_the_explicit_action() -> None:
    task = _recovery_task()
    task.selected_actions = ["apply_selected_feedback"]

    assert _length_recovery_instructions(task) == ""


def test_revision_instructions_require_targeted_answers_in_new_spoken_text() -> None:
    assert "优先把这些 Source 中真正新增的具体场景" in _REVISION_INSTRUCTIONS
    assert "不要把一次“补充素材”修订写成" in _REVISION_INSTRUCTIONS
    assert "比父稿更短的重新起稿" in _REVISION_INSTRUCTIONS


def test_revision_repair_rule_explains_safe_granular_validation_failures() -> None:
    assert "根对象只能包含 patch_version" in _repair_rule_instruction(
        "podcast_revision_patch_schema_invalid"
    )
    assert "根对象只能包含" in _repair_rule_instruction("podcast_revision_schema_invalid")
    assert "allowed_source_refs" in _repair_rule_instruction(
        "podcast_revision_invalid_source_reference"
    )
    assert "逐字复制 editor_bundle.topic" in _repair_rule_instruction(
        "podcast_revision_title_topic_mismatch"
    )
    assert "style_only" in _repair_rule_instruction("podcast_revision_writing_style_sample_leak")
    assert "priority_recovery_source_segments" in _repair_rule_instruction(
        "podcast_revision_recovery_material_unused"
    )


def test_revision_repair_rule_never_reflects_unknown_persisted_text() -> None:
    unknown = "unsafe arbitrary diagnostic text"

    assert _repair_rule_instruction(unknown) == ""
