from __future__ import annotations

from epiphany.revision_schemas import (
    DraftLengthRecoveryPlan,
    PodcastRevisionTaskInput,
)
from epiphany.runtime.revision_prompts import _length_recovery_instructions


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


def test_length_recovery_prompt_is_absent_without_the_explicit_action() -> None:
    task = _recovery_task()
    task.selected_actions = ["apply_selected_feedback"]

    assert _length_recovery_instructions(task) == ""
