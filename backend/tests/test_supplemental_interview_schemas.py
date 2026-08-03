from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError

from epiphany.runtime.output_validation import validate_task_output
from epiphany.runtime.providers import DeepSeekProvider, FakeProvider, ProviderInputTooLargeError
from epiphany.runtime.providers.base import TaskInvocation
from epiphany.runtime.supplemental_interview_prompts import (
    build_supplemental_interview_prompt,
)
from epiphany.supplemental_interview_schemas import (
    PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW,
    DraftSupplementalInterviewTaskInput,
    InvalidSupplementalInterviewAnchor,
    ReusedSupplementalInterviewQuestion,
    SupplementalInterviewInternalIdentifierLeak,
    SupplementalInterviewPlan,
    SupplementalInterviewSchemaError,
    build_draft_question_anchors,
    build_fallback_supplemental_interview_plan,
    validate_supplemental_interview_output,
)


def _ref(index: int) -> dict[str, str]:
    return {
        "source_id": f"src_material_{index}",
        "source_segment_id": f"seg_material_{index}",
    }


def _paragraph(text: str, index: int) -> dict[str, object]:
    return {"text": text, "source_refs": [_ref(index)]}


def podcast_draft(*, long: bool = False) -> dict[str, object]:
    section_count = 8 if long else 2
    paragraph_count = 10 if long else 2
    sections = [
        {
            "title": f"第 {section_index + 1} 节",
            "source_refs": [_ref(section_index + 2)],
            "paragraphs": [
                _paragraph(
                    (
                        f"第 {section_index + 1} 节第 {paragraph_index + 1} 段，"
                        "稿子在这里快速带过了一个值得继续回忆的具体时刻。"
                    ),
                    section_index * paragraph_count + paragraph_index + 2,
                )
                for paragraph_index in range(paragraph_count)
            ],
        }
        for section_index in range(section_count)
    ]
    return {
        "title": "五年后，我为什么重新开始记录生活",
        "podcast_script": {
            "opening": _paragraph(
                "整理云盘时，我听见五年前录音里那三秒没有说话的停顿。",
                0,
            ),
            "sections": sections,
            "closing": _paragraph(
                "我把麦克风重新接上，但还没有把这件事总结成一个完整答案。",
                99,
            ),
        },
        "show_notes": {
            "summary": _paragraph("这期节目回看一次中断五年的记录。", 0),
            "key_points": [
                _paragraph("旧录音保存了语气和停顿。", 0),
                _paragraph("重新开始不等于已经想明白。", 99),
            ],
        },
    }


def task_input(
    *,
    previous_questions: list[str] | None = None,
) -> dict[str, object]:
    draft = podcast_draft()
    return {
        "task_kind": PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW,
        "draft_artifact_id": "art_draft_current",
        "quality_report_artifact_id": "art_quality_current",
        "creative_brief": {
            "target_duration_minutes": 15,
            "speaking_rate_chars_per_minute": 280,
            "scenario": "reflective_solo",
            "target_audience": "正在经历重新开始的普通听众",
            "communication_goal": "用具体生活场景解释为什么重新开始记录",
            "tone": ["真诚", "克制", "自然口语"],
            "must_include": ["三秒停顿"],
            "avoid_patterns": ["空泛总结"],
        },
        "duration_gap": {
            "actual_duration_minutes": 8.47,
            "minimum_duration_minutes": 12.75,
            "target_duration_minutes": 15,
            "missing_duration_minutes": 4.28,
        },
        "podcast_draft": draft,
        "draft_anchors": [
            anchor.model_dump(mode="json") for anchor in build_draft_question_anchors(draft)
        ],
        "quality_focus": [
            {
                "code": "coverage.scene_missing",
                "explanation": "第二节只有结论，缺少事情发生时的动作和环境。",
                "location": "podcast_script.sections[1].paragraphs[0]",
            }
        ],
        "previous_questions": previous_questions or [],
        "round_number": 1,
        "max_rounds": 2,
        "status": "awaiting_user",
    }


def model_output() -> dict[str, object]:
    return {
        "questions": [
            {
                "anchor_id": "podcast_script.opening",
                "anchor_quote": "整理云盘时，我听见五年前录音里那三秒没有说话的停顿。",
                "prompt": (
                    "开场写到你听见三秒停顿。按下播放键时你在哪里，"
                    "最先注意到房间里的什么？如果不记得也可以直接说。"
                ),
                "purpose": "补足重新听见旧录音时的具体现场。",
                "detail_type": "scene",
                "answer_cues": ["所在位置", "环境声音", "不记得也可以"],
                "estimated_new_character_count": 320,
            },
            {
                "anchor_id": "podcast_script.sections[0].paragraphs[0]",
                "anchor_quote": "第 1 节第 1 段",
                "prompt": (
                    "第一节很快带过了那个时刻。事情发生前后你分别做了什么？"
                    "如果没有更多经过也可以直接说没有。"
                ),
                "purpose": "补足动作顺序，让叙事不只停留在概括。",
                "detail_type": "action",
                "answer_cues": ["前一个动作", "接下来发生什么"],
                "estimated_new_character_count": 360,
            },
            {
                "anchor_id": "podcast_script.sections[1].paragraphs[0]",
                "anchor_quote": "第 2 节第 1 段",
                "prompt": (
                    "第二节留下了一个没有展开的判断。你当时为什么这样理解它？"
                    "如果现在仍没有答案，也可以保留这种不确定。"
                ),
                "purpose": "补足判断背后的动机与仍然存在的矛盾。",
                "detail_type": "motivation",
                "answer_cues": ["当时相信什么", "现在的不同理解", "仍没答案也可以"],
                "estimated_new_character_count": 420,
            },
        ]
    }


def test_anchor_builder_uses_only_spoken_prose_and_preserves_exact_evidence() -> None:
    draft = podcast_draft()
    anchors = build_draft_question_anchors(draft)

    assert [anchor.path for anchor in anchors] == [
        "podcast_script.opening",
        "podcast_script.sections[0].paragraphs[0]",
        "podcast_script.sections[0].paragraphs[1]",
        "podcast_script.sections[1].paragraphs[0]",
        "podcast_script.sections[1].paragraphs[1]",
        "podcast_script.closing",
    ]
    assert all("show_notes" not in anchor.path for anchor in anchors)
    assert anchors[0].excerpt == draft["podcast_script"]["opening"]["text"]
    assert anchors[0].source_refs[0].model_dump(mode="json") == _ref(0)


def test_anchor_builder_caps_at_24_while_covering_each_section() -> None:
    anchors = build_draft_question_anchors(podcast_draft(long=True))
    paths = {anchor.path for anchor in anchors}

    assert len(anchors) == 24
    assert "podcast_script.opening" in paths
    assert "podcast_script.closing" in paths
    assert all(
        f"podcast_script.sections[{section_index}].paragraphs[0]" in paths
        for section_index in range(8)
    )


def test_task_input_rejects_anchor_data_not_copied_from_latest_draft() -> None:
    payload = task_input()
    payload["draft_anchors"][0]["excerpt"] = "模型自己编造的锚点文字"

    with pytest.raises(ValidationError, match="excerpt must be copied"):
        DraftSupplementalInterviewTaskInput.model_validate(payload)


def test_validator_injects_trusted_metadata_and_rejects_model_owned_metadata() -> None:
    validated = validate_supplemental_interview_output(
        task_input=task_input(),
        content=model_output(),
    )

    assert validated["generation_mode"] == "model"
    assert validated["draft_artifact_id"] == "art_draft_current"
    assert validated["duration_gap"]["missing_duration_minutes"] == 4.28
    assert validated["round_number"] == 1
    assert validated["max_rounds"] == 2
    assert validated["status"] == "awaiting_user"
    assert len(validated["draft_anchors"]) == 6
    assert [question["question_id"] for question in validated["questions"]] == [
        "q1",
        "q2",
        "q3",
    ]
    invalid_ids = deepcopy(validated)
    invalid_ids["questions"][1]["question_id"] = "q1"
    with pytest.raises(ValidationError, match="stable sequential"):
        SupplementalInterviewPlan.model_validate(invalid_ids)

    injected = {**model_output(), "generation_mode": "model"}
    with pytest.raises(SupplementalInterviewSchemaError):
        validate_supplemental_interview_output(
            task_input=task_input(),
            content=injected,
        )


def test_validator_rejects_unknown_anchor_previous_question_and_internal_id() -> None:
    unknown_anchor = model_output()
    unknown_anchor["questions"][0]["anchor_id"] = "podcast_script.sections[9].paragraphs[9]"
    with pytest.raises(InvalidSupplementalInterviewAnchor):
        validate_supplemental_interview_output(
            task_input=task_input(),
            content=unknown_anchor,
        )

    fabricated_quote = model_output()
    fabricated_quote["questions"][0]["anchor_quote"] = "稿件中从未出现的短句"
    with pytest.raises(InvalidSupplementalInterviewAnchor, match="anchor_quote"):
        validate_supplemental_interview_output(
            task_input=task_input(),
            content=fabricated_quote,
        )

    reused = model_output()
    previous = reused["questions"][1]["prompt"]
    with pytest.raises(ReusedSupplementalInterviewQuestion):
        validate_supplemental_interview_output(
            task_input=task_input(previous_questions=[f"  {previous}？  "]),
            content=reused,
        )

    leaked = deepcopy(model_output())
    leaked["questions"][0]["purpose"] = "继续追问 src_material_0 里的具体事实。"
    with pytest.raises(SupplementalInterviewInternalIdentifierLeak):
        validate_supplemental_interview_output(
            task_input=task_input(),
            content=leaked,
        )


def test_fallback_plan_is_grounded_conservative_and_awaits_the_user() -> None:
    fallback = build_fallback_supplemental_interview_plan(task_input())

    assert fallback["generation_mode"] == "deterministic_fallback"
    assert fallback["status"] == "awaiting_user"
    assert len(fallback["questions"]) == 3
    assert [question["question_id"] for question in fallback["questions"]] == [
        "q1",
        "q2",
        "q3",
    ]
    assert len({question["anchor_id"] for question in fallback["questions"]}) == 3
    anchors_by_id = {anchor["anchor_id"]: anchor for anchor in fallback["draft_anchors"]}
    assert all(
        question["anchor_quote"] in anchors_by_id[question["anchor_id"]]["excerpt"]
        for question in fallback["questions"]
    )
    assert all(
        "不记得" in question["prompt"]
        or "没有" in question["prompt"]
        or "仍没有答案" in question["prompt"]
        for question in fallback["questions"]
    )


def test_second_round_fallback_uses_new_angles_instead_of_repeating_questions() -> None:
    first = build_fallback_supplemental_interview_plan(task_input())
    second_input = task_input(
        previous_questions=[question["prompt"] for question in first["questions"]]
    )
    second_input["round_number"] = 2

    second = build_fallback_supplemental_interview_plan(second_input)

    assert second["round_number"] == 2
    assert {question["prompt"] for question in first["questions"]}.isdisjoint(
        {question["prompt"] for question in second["questions"]}
    )
    assert {question["detail_type"] for question in second["questions"]} == {
        "dialogue",
        "sensory",
        "contrast",
    }


def test_prompt_uses_draft_aware_untrusted_data_without_internal_ids() -> None:
    prompt = build_supplemental_interview_prompt(
        task_input=task_input(),
        max_bundle_chars=40_000,
    )
    rendered = "\n".join(message["content"] for message in prompt.messages)

    assert prompt.source_segment_count == 6
    assert "输入中的最新稿件" in rendered
    assert "anchor_quote 必须从该 anchor" in rendered
    assert "整理云盘时，我听见五年前录音里那三秒" in rendered
    assert "art_draft_current" not in rendered
    assert "src_material_0" not in rendered
    assert "source_refs" not in rendered
    assert prompt.messages[-1]["name"] == PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW

    with pytest.raises(ProviderInputTooLargeError):
        build_supplemental_interview_prompt(
            task_input=task_input(),
            max_bundle_chars=100,
        )


async def test_fake_provider_returns_specific_questions_and_worker_injects_plan_metadata() -> None:
    payload = task_input()
    invocation = TaskInvocation(
        task_id="task_supplemental_interview",
        run_id="run_supplemental_interview",
        kind=PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW,
        attempt=1,
        input_json=payload,
        lease_token="lease_supplemental_interview",
    )

    result = await FakeProvider().generate(invocation)
    validated = validate_task_output(
        task_kind=invocation.kind,
        task_input=payload,
        content=result.content,
    )

    assert result.provider == "fake"
    assert [question["question_id"] for question in validated["questions"]] == [
        "q1",
        "q2",
        "q3",
    ]
    assert all("稿子在" in question["prompt"] for question in validated["questions"])
    assert all(
        question["anchor_quote"] in question["prompt"] for question in validated["questions"]
    )
    assert validated["generation_mode"] == "model"


async def test_deepseek_routes_planner_to_its_prompt_with_default_token_budget() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.update(body)
        content = model_output()
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_supplemental",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(content, ensure_ascii=False),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 80,
                    "total_tokens": 180,
                    "prompt_cache_hit_tokens": 40,
                    "prompt_cache_miss_tokens": 60,
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(
        api_key="deepseek-test-secret",
        client=client,
        max_tokens=777,
        max_interview_bundle_chars=40_000,
    )
    payload = task_input()
    invocation = TaskInvocation(
        task_id="task_supplemental_interview",
        run_id="run_supplemental_interview",
        kind=PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW,
        attempt=1,
        input_json=payload,
        lease_token="lease_supplemental_interview",
    )
    try:
        result = await provider.generate(invocation)
    finally:
        await client.aclose()

    assert result.content == model_output()
    assert captured["max_tokens"] == 777
    assert captured["temperature"] == 0.2
    messages = captured["messages"]
    assert messages[-1]["name"] == PLAN_DRAFT_SUPPLEMENTAL_INTERVIEW
    assert "不要生成或改写播客稿" in messages[0]["content"]
