from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from epiphany.draft_quality_schemas import (
    REVIEW_DIMENSIONS,
    REVIEW_PODCAST_DRAFT,
    ModelSelfReviewTaskInput,
)
from epiphany.editor_schemas import BUILD_PODCAST_DRAFT
from epiphany.interview_schemas import BUILD_INTERVIEW_SCAFFOLD
from epiphany.revision_schemas import (
    REVISE_PODCAST_DRAFT,
    PodcastRevisionTaskInput,
    revision_base_editor_input,
)
from epiphany.runtime.providers.base import (
    ProviderResult,
    RetryableProviderError,
    TaskInvocation,
)

_TIME_EXPRESSION = re.compile(
    r"(?:"
    r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?|"
    r"\d+\s*(?:年|个月|月|周|天)前|"
    r"前几天|那一年|那时候|当时|后来|现在|今天|第一次"
    r")"
)
_THEME_TERMS = (
    "声音",
    "时间",
    "记录",
    "播客",
    "写作",
    "创作",
    "成长",
    "变化",
    "选择",
    "计划",
    "工作",
    "学习",
    "关系",
    "家人",
    "朋友",
    "城市",
    "旅行",
    "身体",
    "情绪",
    "焦虑",
    "期待",
    "害怕",
    "自由",
    "归属",
    "未来",
    "过去",
    "生活",
    "身份",
)


def _clean_text(text: str) -> str:
    return " ".join(text.split())


def _clip(text: str, limit: int) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 1:
        return "…"[:limit]
    max_body_length = limit - 1
    for body_length in range(max_body_length, -1, -1):
        clipped = cleaned[:body_length].rstrip("，。！？；：,.!?;: ")
        closing_marks = "".join(
            closing
            for opening, closing in (("“", "”"), ("‘", "’"), ("《", "》"))
            if clipped.count(opening) > clipped.count(closing)
        )
        if len(clipped) + len(closing_marks) <= max_body_length:
            return f"{clipped}{closing_marks}…"
    return "…"


def _without_terminal_punctuation(text: str) -> str:
    return text.rstrip("，。！？；：,.!?;: ")


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def _topic_ngrams(topic: str) -> set[str]:
    compact = re.sub(r"[\s，。！？；：,.!?;:、“”‘’（）()《》【】\[\]\-_/]+", "", topic)
    return {
        compact[start : start + size]
        for size in (2, 3, 4)
        for start in range(0, max(0, len(compact) - size + 1))
    }


def _topic_relevance(text: str, topic: str) -> int:
    if not topic.strip():
        return 0
    return sum(len(fragment) ** 2 for fragment in _topic_ngrams(topic) if fragment in text)


def _representative_segments(
    segments: list[dict[str, Any]],
    *,
    topic: str,
    purpose: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Choose one topic-relevant segment per Source before reusing a Source."""

    by_source: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for position, segment in enumerate(segments):
        by_source.setdefault(str(segment["source_id"]), []).append((position, segment))

    source_winners: list[tuple[int, tuple[int, int, int, int], dict[str, Any]]] = []
    for source_position, source_segments in enumerate(by_source.values()):
        best_position, best = max(
            source_segments,
            key=lambda item: (
                _topic_relevance(str(item[1]["text"]), topic),
                (
                    1
                    if purpose == "timeline" and _TIME_EXPRESSION.search(str(item[1]["text"]))
                    else 0
                ),
                (
                    sum(term in str(item[1]["text"]) for term in _THEME_TERMS)
                    if purpose == "theme"
                    else 0
                ),
                -item[0],
            ),
        )
        best_score = (
            _topic_relevance(str(best["text"]), topic),
            1 if purpose == "timeline" and _TIME_EXPRESSION.search(str(best["text"])) else 0,
            sum(term in str(best["text"]) for term in _THEME_TERMS) if purpose == "theme" else 0,
            -best_position,
        )
        source_winners.append((source_position, best_score, best))

    if len(source_winners) <= limit:
        return [winner for _, _, winner in source_winners]

    source_winners.sort(
        key=lambda item: (*item[1], -item[0]),
        reverse=True,
    )
    selected = [winner for _, _, winner in source_winners[:limit]]

    selected_ids = {str(segment["source_segment_id"]) for segment in selected}
    remaining = [
        (position, segment)
        for position, segment in enumerate(segments)
        if str(segment["source_segment_id"]) not in selected_ids
    ]
    remaining.sort(
        key=lambda item: (
            -_topic_relevance(str(item[1]["text"]), topic),
            item[0],
        )
    )
    selected.extend(segment for _, segment in remaining[: max(0, limit - len(selected))])
    return selected


def _timeline_sentence(text: str) -> str:
    sentences = _sentences(text)
    if not sentences:
        return text.strip()
    return next(
        (sentence for sentence in sentences if _TIME_EXPRESSION.search(sentence)), sentences[0]
    )


def _theme_sentence(text: str) -> str:
    sentences = _sentences(text)
    if not sentences:
        return text.strip()
    return max(
        sentences,
        key=lambda sentence: (
            sum(term in sentence for term in _THEME_TERMS),
            min(len(sentence), 360),
        ),
    )


def _timeline_label(text: str, index: int) -> tuple[str, str | None]:
    cleaned = _clean_text(text).strip("，。！？；：,.!?;: ")
    time_match = _TIME_EXPRESSION.search(cleaned)
    time_expression = time_match.group(0) if time_match else None
    headline = cleaned
    if time_match and time_match.start() == 0:
        headline = cleaned[time_match.end() :].lstrip("，。！？；：,.!?;: ")
    headline = _clip(headline or cleaned, 28)
    if time_expression:
        return f"{time_expression}：{headline}", time_expression
    return f"线索 {index}：{headline}", None


def _theme_label(text: str) -> str:
    positioned_terms = sorted(
        ((position, term) for term in _THEME_TERMS if (position := text.find(term)) >= 0),
        key=lambda item: (item[0], item[1]),
    )
    terms: list[str] = []
    for _, term in positioned_terms:
        if any(term in existing or existing in term for existing in terms):
            continue
        terms.append(term)
        if len(terms) == 2:
            break
    if terms:
        return "与".join(terms)
    return f"“{_clip(text, 16)}”背后的变化"


def _source_ref(item: dict[str, Any]) -> dict[str, str]:
    return {
        "source_id": str(item["source_id"]),
        "source_segment_id": str(item["source_segment_id"]),
    }


def _merge_refs(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for reference in group:
            key = (reference["source_id"], reference["source_segment_id"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(reference)
    return merged


def _spoken_script_character_count(content: dict[str, Any]) -> int:
    script = content["podcast_script"]
    texts = [
        str(script["opening"]["text"]),
        *[
            str(paragraph["text"])
            for section in script["sections"]
            for paragraph in section["paragraphs"]
        ],
        str(script["closing"]["text"]),
    ]
    return sum(len("".join(text.split())) for text in texts)


class FakeProvider:
    """Deterministic provider used to exercise runtime behavior without an API key."""

    name = "fake"
    model = "fake-v1"
    billing_currency = "USD"

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        configured_failures = self._configured_failures(invocation.input_json)
        if invocation.attempt <= configured_failures:
            raise RetryableProviderError(
                f"injected transient failure for {invocation.kind} on attempt {invocation.attempt}"
            )

        handlers = {
            "prepare_sources": self._prepare_sources,
            "fake_research": self._fake_research,
            "assemble_artifact": self._assemble_artifact,
            "timeline_research": self._timeline_research,
            "theme_research": self._theme_research,
            BUILD_INTERVIEW_SCAFFOLD: self._build_interview_scaffold,
            BUILD_PODCAST_DRAFT: self._build_podcast_draft,
            REVISE_PODCAST_DRAFT: self._revise_podcast_draft,
            REVIEW_PODCAST_DRAFT: self._review_podcast_draft,
        }
        try:
            content = handlers[invocation.kind](invocation.input_json)
        except KeyError as error:
            raise ValueError(f"unsupported fake task kind: {invocation.kind}") from error

        return ProviderResult(content=content, provider=self.name, model=self.model)

    @staticmethod
    def _configured_failures(input_json: dict[str, Any]) -> int:
        run_payload = input_json.get("run_payload", {})
        failures = run_payload.get("fake_failures", {})
        value = failures.get(input_json.get("task_kind"), 0)
        return int(value)

    @staticmethod
    def _prepare_sources(input_json: dict[str, Any]) -> dict[str, Any]:
        payload = input_json.get("run_payload", {})
        return {
            "stage": "sources_prepared",
            "normalized_payload": payload,
            "source_count": len(payload.get("sources", [])),
        }

    @staticmethod
    def _fake_research(input_json: dict[str, Any]) -> dict[str, Any]:
        previous = input_json.get("previous_content", {})
        topic = previous.get("normalized_payload", {}).get("topic", "untitled reflection")
        return {
            "stage": "research_complete",
            "topic": topic,
            "observations": [
                {
                    "text": f"A deterministic observation about {topic}.",
                    "source_refs": [],
                }
            ],
            "source_artifact_id": input_json.get("previous_artifact_id"),
        }

    @staticmethod
    def _assemble_artifact(input_json: dict[str, Any]) -> dict[str, Any]:
        previous = input_json.get("previous_content", {})
        return {
            "stage": "artifact_assembled",
            "title": f"Fake draft: {previous.get('topic', 'untitled reflection')}",
            "summary": "The durable three-step workflow completed without a model call.",
            "research_artifact_id": input_json.get("previous_artifact_id"),
            "observations": previous.get("observations", []),
        }

    @staticmethod
    def _timeline_research(input_json: dict[str, Any]) -> dict[str, Any]:
        topic = str(input_json.get("topic") or "")
        segments = _representative_segments(
            input_json["source_segments"],
            topic=topic,
            purpose="timeline",
        )
        events: list[dict[str, Any]] = []
        for index, segment in enumerate(segments, start=1):
            sentence = _timeline_sentence(str(segment["text"]))
            label, time_expression = _timeline_label(sentence, index)
            events.append(
                {
                    "label": label,
                    "description": _clip(sentence, 600),
                    "time_expression": time_expression,
                    "confidence": 0.9 if time_expression else 0.78,
                    "source_refs": [_source_ref(segment)],
                }
            )
        return {
            "timeline_events": events,
            "open_questions": [],
        }

    @staticmethod
    def _theme_research(input_json: dict[str, Any]) -> dict[str, Any]:
        topic = str(input_json.get("topic") or "")
        segments = _representative_segments(
            input_json["source_segments"],
            topic=topic,
            purpose="theme",
        )
        themes: list[dict[str, Any]] = []
        quotes: list[dict[str, Any]] = []
        for segment in segments:
            sentence = _theme_sentence(str(segment["text"]))
            source_ref = _source_ref(segment)
            label = _theme_label(sentence)
            quote = sentence[: min(len(sentence), 360)].strip()
            themes.append(
                {
                    "theme": label,
                    "insight": (f"素材把“{label}”放进了一段具体经历：{_clip(sentence, 500)}"),
                    "confidence": 0.86,
                    "source_refs": [source_ref],
                }
            )
            quotes.append(
                {
                    "quote": quote,
                    "context": f"素材中直接呈现“{label}”的原话。",
                    "source_ref": source_ref,
                }
            )
        return {
            "themes": themes,
            "quotes": quotes,
        }

    @staticmethod
    def _build_interview_scaffold(input_json: dict[str, Any]) -> dict[str, Any]:
        timeline_events = input_json["timeline"]["timeline_events"]
        themes = input_json["themes"]["themes"]
        quotes = input_json["themes"].get("quotes", [])
        timeline_event = timeline_events[0]
        theme = themes[-1]
        quote = quotes[min(1, len(quotes) - 1)] if quotes else None
        timeline_refs = list(timeline_event["source_refs"])
        theme_refs = list(theme["source_refs"])
        quote_refs = [quote["source_ref"]] if quote else theme_refs
        overview_refs = _merge_refs(timeline_refs, theme_refs)
        topic = input_json["topic"]
        event_label = _clip(str(timeline_event["label"]), 70)
        event_description = _without_terminal_punctuation(
            _clip(str(timeline_event["description"]), 120)
        )
        theme_label = _clip(str(theme["theme"]), 60)
        quoted_text = _without_terminal_punctuation(
            _clip(
                str(quote["quote"]) if quote else str(theme["insight"]),
                100,
            )
        )
        return {
            "title": topic,
            "episode_intent": {
                "text": (
                    f"以{event_label}为入口，理解“{theme_label}”"
                    "如何从一段经历变成今天仍值得记录的变化。"
                ),
                "source_refs": overview_refs,
            },
            "opening": {
                "text": (
                    f"围绕“{topic}”，素材已经留下一个明确时间节点：{event_label}；"
                    f"也出现了“{theme_label}”这条线索。先回到现场，再沿着变化讲到现在。"
                ),
                "source_refs": overview_refs,
            },
            "sections": [
                {
                    "title": "回到事情发生的时刻",
                    "source_refs": timeline_refs,
                    "known_context": [
                        {
                            "text": str(timeline_event["description"]),
                            "source_refs": timeline_refs,
                        }
                    ],
                    "transition": {
                        "text": (f"先不急着总结，从{event_label}这个节点回到当时的具体场景。"),
                        "source_refs": timeline_refs,
                    },
                    "questions": [
                        {
                            "prompt": (
                                f"“{event_description}”发生时，你具体在哪里、正在做什么？"
                                "最先注意到的一个画面、声音或身体感觉是什么？"
                            ),
                            "purpose": "把素材中的事件还原成听众能够进入的具体现场。",
                            "keywords": ["现场", "感官"],
                            "source_refs": timeline_refs,
                        },
                        {
                            "prompt": (
                                "这件事发生之前，你对接下来有什么预设？"
                                "现实和原来的预设最早从哪里开始不一样？"
                            ),
                            "purpose": "补充事件发生前的期待，以及变化真正启动的节点。",
                            "keywords": ["预设", "转折"],
                            "source_refs": timeline_refs,
                        },
                    ],
                },
                {
                    "title": "听见当时真正的自己",
                    "source_refs": quote_refs,
                    "known_context": [
                        {
                            "text": str(quote["quote"]) if quote else str(theme["insight"]),
                            "source_refs": quote_refs,
                        }
                    ],
                    "transition": {
                        "text": (
                            f"素材里留下了一句很具体的话：“{quoted_text}”。"
                            "先保留它原来的语气，再补上当时没有说出的部分。"
                        ),
                        "source_refs": quote_refs,
                    },
                    "questions": [
                        {
                            "prompt": (
                                f"当你说“{quoted_text}”时，前后具体发生了什么？"
                                "哪一种情绪最接近当时真实的状态？"
                            ),
                            "purpose": "把原话放回它发生的语境，补充没有被文字保存下来的情绪。",
                            "keywords": ["原话", "情绪"],
                            "source_refs": quote_refs,
                        },
                        {
                            "prompt": (
                                "这句话里，哪一个词现在听来意义已经变了？"
                                "你今天还会用同一个词描述那时的自己吗？"
                            ),
                            "purpose": "通过措辞的变化，辨认当时与现在看待自己的差异。",
                            "keywords": ["措辞", "自我理解"],
                            "source_refs": quote_refs,
                        },
                    ],
                },
                {
                    "title": "把这段变化连接到现在",
                    "source_refs": theme_refs,
                    "known_context": [
                        {
                            "text": str(theme["insight"]),
                            "source_refs": theme_refs,
                        }
                    ],
                    "transition": {
                        "text": (
                            f"从当时的经验回到现在，看看“{theme_label}”怎样延续进后来真实的生活。"
                        ),
                        "source_refs": theme_refs,
                    },
                    "questions": [
                        {
                            "prompt": (
                                f"现在回头看，“{theme_label}”真正改变的是一个决定、"
                                "一种关系，还是你理解自己的方式？请从后来发生的一个例子讲起。"
                            ),
                            "purpose": "用后来的事实验证这段经历是否真的形成了持续变化。",
                            "keywords": ["后来", "改变"],
                            "source_refs": theme_refs,
                        },
                        {
                            "prompt": (
                                "如果不急着把这段经历总结成道理，"
                                "你最想保留其中哪一处矛盾，或者哪件仍没想明白的事？"
                            ),
                            "purpose": "保留经历的复杂度，为后续口述留下真实的开放空间。",
                            "keywords": ["矛盾", "未完成"],
                            "source_refs": theme_refs,
                        },
                    ],
                },
            ],
            "material_gaps": [
                {
                    "gap": (
                        f"素材已经指出“{theme_label}”，但从当时到现在的关键转折还没有被完整讲出。"
                    ),
                    "why_it_matters": (
                        "补上中间发生的一个决定或具体事件，"
                        "能让听众理解变化是怎样发生的，而不只听到事后的结论。"
                    ),
                    "source_refs": theme_refs,
                }
            ],
            "closing": {
                "text": (
                    f"先从{event_label}最有画面的部分开始讲，"
                    f"再顺着“{theme_label}”追到今天；新的记忆出现时，就沿着它继续。"
                ),
                "source_refs": overview_refs,
            },
        }

    @staticmethod
    def _build_podcast_draft(input_json: dict[str, Any]) -> dict[str, Any]:
        """Create readable, deterministic Editor output from real test material."""

        topic = str(input_json["topic"])
        scaffold = input_json["interview_scaffold"]
        initial_segments = input_json["initial_source_segments"]
        supplemental_segments = input_json["supplemental_source_segments"]

        initial_refs = [_source_ref(segment) for segment in initial_segments]
        supplemental_refs = [_source_ref(segment) for segment in supplemental_segments]
        overview_refs = _merge_refs([initial_refs[0]], [supplemental_refs[0]])

        sections: list[dict[str, Any]] = []
        for section in scaffold["sections"]:
            paragraphs: list[dict[str, Any]] = []
            for context in section["known_context"]:
                paragraphs.append(
                    {
                        "text": _clip(str(context["text"]), 600),
                        "source_refs": list(context["source_refs"]),
                    }
                )
            if not paragraphs:
                transition = section["transition"]
                paragraphs.append(
                    {
                        "text": _clip(str(transition["text"]), 600),
                        "source_refs": list(transition["source_refs"]),
                    }
                )
            sections.append(
                {
                    "title": str(section["title"]),
                    "source_refs": _merge_refs(
                        list(section["source_refs"]),
                        *[list(paragraph["source_refs"]) for paragraph in paragraphs],
                    ),
                    "paragraphs": paragraphs,
                }
            )

        supplemental_paragraphs = [
            {
                "text": (
                    "补充口述把这段经历带回了一个更具体的瞬间："
                    f"{_clip(_sentences(str(segment['text']))[0], 520)}"
                ),
                "source_refs": [_source_ref(segment)],
            }
            for segment in supplemental_segments[:3]
            if _sentences(str(segment["text"]))
        ]
        sections[-1]["paragraphs"].extend(supplemental_paragraphs)
        sections[-1]["source_refs"] = _merge_refs(
            *[list(paragraph["source_refs"]) for paragraph in supplemental_paragraphs],
            list(sections[-1]["source_refs"]),
        )[:10]

        initial_sentence = _clip(
            _sentences(str(initial_segments[0]["text"]))[0],
            360,
        )
        supplemental_sentence = _clip(
            _sentences(str(supplemental_segments[0]["text"]))[0],
            360,
        )
        return {
            "title": topic,
            "podcast_script": {
                "opening": {
                    "text": str(scaffold["opening"]["text"]),
                    "source_refs": list(scaffold["opening"]["source_refs"]),
                },
                "sections": sections,
                "closing": {
                    "text": str(scaffold["closing"]["text"]),
                    "source_refs": list(scaffold["closing"]["source_refs"]),
                },
            },
            "show_notes": {
                "summary": {
                    "text": (
                        f"这一期从“{topic}”出发，把已有记录与后来补充的口述放在一起，"
                        "保留变化发生时的具体细节。"
                    ),
                    "source_refs": overview_refs,
                },
                "key_points": [
                    {
                        "text": f"已有记录留下的起点：{initial_sentence}",
                        "source_refs": [initial_refs[0]],
                    },
                    {
                        "text": f"补充口述带回的新细节：{supplemental_sentence}",
                        "source_refs": [supplemental_refs[0]],
                    },
                    {
                        "text": "已有记录与补充口述会在这一期被放在一起回看。",
                        "source_refs": overview_refs,
                    },
                ],
            },
        }

    @staticmethod
    def _revise_podcast_draft(input_json: dict[str, Any]) -> dict[str, Any]:
        """Produce a deterministic changed candidate from real factual material."""

        parsed = PodcastRevisionTaskInput.model_validate(input_json)
        base_input = revision_base_editor_input(parsed.model_dump(mode="json"))
        if (
            "reuse_unused_material" in parsed.selected_actions
            and parsed.length_recovery_plan is not None
        ):
            revised = deepcopy(parsed.parent_podcast_draft.model_dump(mode="json"))
            factual_segments = [
                *base_input["initial_source_segments"],
                *base_input["supplemental_source_segments"],
            ]
            segment_by_key = {
                (
                    str(segment["source_id"]),
                    str(segment["source_segment_id"]),
                ): segment
                for segment in factual_segments
            }
            existing_texts = {
                _clean_text(str(revised["podcast_script"]["opening"]["text"])),
                _clean_text(str(revised["podcast_script"]["closing"]["text"])),
                *(
                    _clean_text(str(paragraph["text"]))
                    for section in revised["podcast_script"]["sections"]
                    for paragraph in section["paragraphs"]
                ),
            }
            current_characters = _spoken_script_character_count(revised)
            minimum_characters = parsed.length_recovery_plan.minimum_script_character_count
            maximum_characters = parsed.length_recovery_plan.maximum_script_character_count
            added_paragraphs: list[dict[str, Any]] = []
            section_cursor = 0

            for reference in parsed.length_recovery_plan.priority_unused_source_refs:
                if current_characters >= minimum_characters:
                    break
                segment = segment_by_key.get((reference.source_id, reference.source_segment_id))
                if segment is None:
                    continue
                available_room = maximum_characters - current_characters
                if available_room <= 0:
                    break
                text = _clip(
                    str(segment["text"]),
                    min(600, available_room),
                )
                normalized_text = _clean_text(text)
                if not normalized_text or normalized_text in existing_texts:
                    continue

                sections = revised["podcast_script"]["sections"]
                selected_section: dict[str, Any] | None = None
                for offset in range(len(sections)):
                    candidate_index = (section_cursor + offset) % len(sections)
                    if len(sections[candidate_index]["paragraphs"]) < 10:
                        selected_section = sections[candidate_index]
                        section_cursor = (candidate_index + 1) % len(sections)
                        break
                if selected_section is None:
                    if len(sections) >= 8:
                        break
                    selected_section = {
                        "title": "继续展开的具体场景",
                        "source_refs": [_source_ref(segment)],
                        "paragraphs": [],
                    }
                    sections.append(selected_section)
                    section_cursor = 0

                paragraph = {
                    "text": text,
                    "source_refs": [_source_ref(segment)],
                }
                selected_section["paragraphs"].append(paragraph)
                selected_section["source_refs"] = _merge_refs(
                    list(selected_section["source_refs"]),
                    list(paragraph["source_refs"]),
                )[:10]
                added_paragraphs.append(paragraph)
                existing_texts.add(normalized_text)
                current_characters += len("".join(text.split()))

            if added_paragraphs:
                revised["show_notes"]["key_points"][-1] = {
                    "text": _clip(added_paragraphs[-1]["text"], 360),
                    "source_refs": list(added_paragraphs[-1]["source_refs"]),
                }
            return revised

        revised = FakeProvider._build_podcast_draft(base_input)
        factual_segments = [
            *base_input["initial_source_segments"],
            *base_input["supplemental_source_segments"],
        ]
        opening_segment = factual_segments[0]
        opening_sentences = _sentences(str(opening_segment["text"]))
        revised["podcast_script"]["opening"] = {
            "text": _clip(
                opening_sentences[0] if opening_sentences else str(opening_segment["text"]),
                600,
            ),
            "source_refs": [_source_ref(opening_segment)],
        }

        cited_keys = {
            (
                reference["source_id"],
                reference["source_segment_id"],
            )
            for section in revised["podcast_script"]["sections"]
            for paragraph in section["paragraphs"]
            for reference in paragraph["source_refs"]
        }
        unused_segments = [
            segment
            for segment in factual_segments
            if (
                str(segment["source_id"]),
                str(segment["source_segment_id"]),
            )
            not in cited_keys
        ]
        last_section = revised["podcast_script"]["sections"][-1]
        for segment in unused_segments[:2]:
            sentences = _sentences(str(segment["text"]))
            if not sentences or len(last_section["paragraphs"]) >= 10:
                continue
            paragraph = {
                "text": _clip(sentences[0], 600),
                "source_refs": [_source_ref(segment)],
            }
            last_section["paragraphs"].append(paragraph)
            last_section["source_refs"] = _merge_refs(
                list(last_section["source_refs"]),
                list(paragraph["source_refs"]),
            )[:10]
        revised["show_notes"]["key_points"][-1] = {
            "text": _clip(
                _sentences(str(factual_segments[-1]["text"]))[0],
                360,
            ),
            "source_refs": [_source_ref(factual_segments[-1])],
        }
        return revised

    @staticmethod
    def _review_podcast_draft(input_json: dict[str, Any]) -> dict[str, Any]:
        """Return evidence-bearing review cards from the real Draft text.

        These fixed scores are a deterministic contract fixture, not a content
        quality judgment. The strict Worker validator still checks every quote
        and source reference.
        """

        parsed = ModelSelfReviewTaskInput.model_validate(input_json)
        draft = parsed.podcast_draft
        evidence_blocks = [
            (
                "podcast_script.opening",
                draft.podcast_script.opening.text,
                draft.podcast_script.opening.source_refs,
            ),
            *[
                (
                    f"podcast_script.sections[{section_index}].paragraphs[0]",
                    section.paragraphs[0].text,
                    section.paragraphs[0].source_refs,
                )
                for section_index, section in enumerate(draft.podcast_script.sections)
            ],
            (
                "podcast_script.closing",
                draft.podcast_script.closing.text,
                draft.podcast_script.closing.source_refs,
            ),
            (
                "show_notes.summary",
                draft.show_notes.summary.text,
                draft.show_notes.summary.source_refs,
            ),
        ]
        assessment_by_dimension = {
            "brief_adherence": "该段提供了可核对创作约束贴合度的真实初稿证据。",
            "source_faithfulness": "该段及其来源引用可用于核对初稿是否保留原始事实限定。",
            "coverage_and_specificity": "该段呈现了可检查具体场景与信息密度的真实初稿内容。",
            "structure_and_coherence": "该段在初稿中的位置可用于检查叙事推进与衔接。",
            "oral_naturalness_and_voice_fit": "该段保留了可直接检查口播自然度的真实措辞。",
            "conciseness_and_non_redundancy": "该段可与其余初稿比较是否存在重复或信息增量不足。",
        }
        scores = {
            "brief_adherence": 4,
            "source_faithfulness": 4,
            "coverage_and_specificity": 3,
            "structure_and_coherence": 4,
            "oral_naturalness_and_voice_fit": 3,
            "conciseness_and_non_redundancy": 4,
        }
        dimensions: list[dict[str, Any]] = []
        for index, dimension in enumerate(REVIEW_DIMENSIONS):
            location, text, references = evidence_blocks[index % len(evidence_blocks)]
            dimensions.append(
                {
                    "dimension": dimension,
                    "assessable": True,
                    "score": scores[dimension],
                    "assessment": assessment_by_dimension[dimension],
                    "limitation": None,
                    "evidence": [
                        {
                            "location": location,
                            "exact_quote": text[: min(180, len(text))],
                            "source_refs": [
                                reference.model_dump(mode="json") for reference in references[:2]
                            ],
                        }
                    ],
                }
            )
        if parsed.writing_style_context_status == "ready":
            style_segment = parsed.writing_style_segments[0]
            location, text, references = evidence_blocks[0]
            style_excerpt = next(
                (line.strip()[:180] for line in style_segment.text.splitlines() if line.strip()),
                style_segment.text.strip()[:180],
            )
            dimensions.append(
                {
                    "dimension": "personal_style_match",
                    "assessable": True,
                    "score": 3,
                    "assessment": (
                        "该初稿片段与用户主动提供的写作样本可用于比较句式、节奏和口语感；"
                        "这只是风格建议，不是作者身份判断。"
                    ),
                    "limitation": None,
                    "evidence": [
                        {
                            "location": location,
                            "exact_quote": text[: min(180, len(text))],
                            "source_refs": [
                                reference.model_dump(mode="json") for reference in references[:2]
                            ],
                        }
                    ],
                    "style_sample_evidence": [
                        {
                            "location": "writing_style_segments[0]",
                            "exact_quote": style_excerpt,
                            "source_ref": {
                                "source_id": style_segment.source_id,
                                "source_segment_id": style_segment.source_segment_id,
                            },
                        }
                    ],
                }
            )
        return {
            "review_kind": "model_self_review",
            "advisory": True,
            "dimensions": dimensions,
        }
