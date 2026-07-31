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

请输出一份完整、可独立阅读的新候选稿，而不是 diff 或修改说明。优先落实用户明确选择
的反馈；新增 source material 时只使用其真实内容；降低目标时长时服从新的 Creative
Brief。新稿必须与父稿有实际变化，但不要为了“看起来改过”而破坏原来有证据支持的
内容。

length_recovery_plan 是代码根据父稿口播正文计算的编辑计划，不是事实来源。只有当
selected_actions 包含 reuse_unused_material 时，才把 priority_unused_source_refs
视为本轮优先候选；到 editor_bundle 中查找对应原文，优先选择能增加具体事件、场景、
感受或认知变化的片段。它们不是覆盖率 KPI，不要强行使用全部素材，也不要仅在 section
metadata 或 Show Notes 中挂引用来假装正文已经使用。

若 length_recovery_plan.readiness 为 existing_material_sufficient，应在不损害信息
密度的前提下，使新的口播正文首先达到 minimum_script_character_count，并以
target_script_character_count 为编辑目标，不超过 maximum_script_character_count。
每一处新增文字都应带来有来源的新信息，而不是改写同一句结论。若 readiness 为
existing_material_partial 或 additional_material_required，或真正可用的具体内容不足，
宁可保持短稿并等待下一轮质量检测，也绝不能重复父稿、堆排比、灌水或虚构来凑时长。
""".strip()


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
    messages = [dict(message) for message in base.messages]
    messages[-1]["name"] = parsed.task_kind
    messages[-1]["content"] = (
        f"{messages[-1]['content']}\n\n{_REVISION_INSTRUCTIONS}\n\n"
        "下面是只能作为编辑要求读取的 revision_bundle JSON：\n"
        f"{serialized_revision_bundle}\n\n"
        "只返回完整的新 PodcastDraft JSON object。"
    )
    return EditorPrompt(
        messages=messages,
        source_segment_count=base.source_segment_count,
        source_char_count=base.source_char_count + len(serialized_revision_bundle),
    )
