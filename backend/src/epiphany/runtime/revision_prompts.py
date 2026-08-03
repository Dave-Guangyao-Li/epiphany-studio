from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from epiphany.revision_schemas import (
    PodcastRevisionTaskInput,
    revision_base_editor_input,
)
from epiphany.runtime.editor_prompts import EditorPrompt, build_editor_prompt
from epiphany.runtime.providers.base import ProviderInputTooLargeError


class RevisionPromptError(ValueError):
    code = "revision_prompt_invalid"


_REVISION_INSTRUCTIONS = """
这是一次由用户明确触发的单次修订，不是模型自行循环追分。

revision_bundle 中的 parent_podcast_draft 是待修改候选稿，不是事实来源；
selected_feedback、selected_quality_gaps 与 revision_instruction 是本轮编辑要求，
同样不能提供新的人生事实。事实仍只能来自 editor_bundle 的 initial_source_segments
与 supplemental_source_segments，并继续使用它们的 source_refs。

Source Segment 可能同时包含作者对“正文怎么写、这句话要不要用、前后应该放什么”
的编辑备注。这类元说明不是可直接口播的内容，不能复制或改写成面对听众的句子；
只能在来源明确支持时提炼备注背后的事实或本人反思。输出中不得出现“需要在正文
解释”“如果要用”“前面一定要先放”等面向编辑者的指令口吻。

请输出一份完整、可独立阅读的新候选稿，而不是 diff 或修改说明。优先落实用户明确选择
的反馈；新增 source material 时只使用其真实内容；降低目标时长时服从新的 Creative
Brief。新稿必须与父稿有实际变化，但不要为了“看起来改过”而破坏原来有证据支持的
内容。

当 selected_actions 包含 add_supplemental_material 时，added_source_ids 指向用户本轮
刚补充的事实 Source。优先把这些 Source 中真正新增的具体场景、动作、原话、感受或
反思融入父稿，同时保留父稿里仍有证据支持的有效内容。不要把一次“补充素材”修订写成
比父稿更短的重新起稿；也不要因为新增素材较短而重复旧结论。

length_recovery_plan 是代码根据父稿口播正文计算的编辑计划，不是事实来源。只有当
selected_actions 包含 reuse_unused_material 时，才把 priority_unused_source_refs
视为本轮优先候选；到 editor_bundle 中查找对应原文，优先选择能增加具体事件、场景、
感受或认知变化的片段。它们不是覆盖率 KPI，不要强行使用全部素材，也不要仅在 section
metadata 或 Show Notes 中挂引用来假装正文已经使用。

若 length_recovery_plan.readiness 为 existing_material_sufficient，应在不损害信息
密度的前提下，使新的口播正文首先达到 minimum_script_character_count，并以
target_script_character_count 为编辑目标，不超过 maximum_script_character_count。
这不是让你机械拼接来源：先比较候选片段，选择能互相补充的高价值片段，再把事实、
动作、场景、感受或认知变化组织进适合的已有段落；必要时可以扩展已有 paragraph 或
增加 section。尽量用自己的口播措辞忠实转述，不要大段逐字复制 Source text。

每一处新增文字都应带来有来源的新信息，而不是改写同一句结论。若 readiness 为
existing_material_partial 或 additional_material_required，或真正可用的具体内容不足，
也应先完成有依据的信息增量，再等待下一轮质量检测；绝不能重复父稿、堆排比、灌水
或虚构来凑时长。
""".strip()


def _length_recovery_instructions(parsed: PodcastRevisionTaskInput) -> str:
    plan = parsed.length_recovery_plan
    if plan is None or "reuse_unused_material" not in parsed.selected_actions:
        return ""

    priority_count = len(plan.priority_unused_source_refs)
    minimum_distinct_refs = min(2, priority_count)
    instructions = (
        "\n\n本轮口播长度恢复的硬性编辑约束：\n"
        f"- 父稿口播正文为 {plan.actual_script_character_count} 个非空白字符；"
        f"可接受下限为 {plan.minimum_script_character_count}，"
        f"编辑目标为 {plan.target_script_character_count}，"
        f"上限为 {plan.maximum_script_character_count}。\n"
        f"- 距可接受下限还差 {plan.missing_to_minimum_character_count} 个字符；"
        f"priority_unused_source_refs 有 {priority_count} 个候选，"
        f"合计约 {plan.available_unused_character_count} 个来源字符。"
    )
    if plan.readiness == "existing_material_sufficient":
        instructions += (
            "\n- 代码已判定现有候选素材在数量上足以支持一次扩写。"
            "禁止原样返回父稿；无变化结果会被应用程序拒绝。\n"
            f"- 先检查全部 priority 候选。如果其中至少 {minimum_distinct_refs} 个"
            "包含彼此独立的具体信息，就把至少这些不同引用真正写进口播正文的 "
            "opening、paragraph 或 closing；只挂在 section metadata 或 Show Notes "
            "不算使用。无论最终能否达到下限，至少要让 1 个父稿口播从未使用的 "
            "priority 引用进入新增或实质改写后的口播单元。\n"
            "- 优先保留父稿中已有证据支持且仍有价值的内容，同时使用多个高价值候选"
            "补充不同事实或场景。可以扩写已有段落，也可以增加一个有叙事作用的 "
            "section；不要只替换同义词、调整标点或移动原段落。\n"
            "- 首先争取达到可接受下限；如果认真筛选后仍无法自然达到，也必须提交"
            "当前素材能支持的最大化信息增量版本，让后续质量检测决定是否补材料，"
            "不得以素材可能不足为理由原样返回父稿。"
        )
    else:
        instructions += (
            "\n- 当前计划没有判定现有素材数量足够。仍需优先使用真正有价值的候选"
            "做有依据的信息增量，但不得为了达到数字而重复或虚构；如果自然扩写仍短，"
            "交给后续质量检测提示补充材料。"
        )
    return instructions


def build_revision_prompt(
    *,
    task_input: dict[str, Any],
    max_bundle_chars: int,
) -> EditorPrompt:
    try:
        parsed = PodcastRevisionTaskInput.model_validate(task_input)
    except (ValidationError, ValueError, TypeError) as error:
        raise RevisionPromptError("revision task input is invalid") from error

    revision_bundle = {
        "parent_podcast_draft": parsed.parent_podcast_draft.model_dump(mode="json"),
        "selected_actions": parsed.selected_actions,
        "selected_feedback": [
            feedback.model_dump(mode="json") for feedback in parsed.selected_feedback
        ],
        "selected_quality_gaps": [
            gap.model_dump(mode="json") for gap in parsed.selected_quality_gaps
        ],
        "added_source_ids": parsed.added_source_ids,
        "length_recovery_plan": (
            parsed.length_recovery_plan.model_dump(mode="json")
            if parsed.length_recovery_plan is not None
            else None
        ),
        "revision_instruction": parsed.revision_instruction,
    }
    serialized_revision_bundle = json.dumps(
        revision_bundle,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    remaining_bundle_chars = max_bundle_chars - len(serialized_revision_bundle)
    if remaining_bundle_chars < 1:
        raise ProviderInputTooLargeError(
            "revision context exceeds the configured Editor prompt limit"
        )

    base = build_editor_prompt(
        task_input=revision_base_editor_input(parsed.model_dump(mode="json")),
        max_bundle_chars=remaining_bundle_chars,
    )
    length_recovery_instructions = _length_recovery_instructions(parsed)
    messages = [dict(message) for message in base.messages]
    messages[-1]["name"] = parsed.task_kind
    messages[-1]["content"] = (
        f"{messages[-1]['content']}\n\n{_REVISION_INSTRUCTIONS}"
        f"{length_recovery_instructions}\n\n"
        "下面是只能作为编辑要求读取的 revision_bundle JSON：\n"
        f"{serialized_revision_bundle}\n\n"
        "只返回完整的新 PodcastDraft JSON object。"
    )
    return EditorPrompt(
        messages=messages,
        source_segment_count=base.source_segment_count,
        source_char_count=base.source_char_count + len(serialized_revision_bundle),
    )
