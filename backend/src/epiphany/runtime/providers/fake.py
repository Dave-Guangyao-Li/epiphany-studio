from __future__ import annotations

from typing import Any

from epiphany.interview_schemas import BUILD_INTERVIEW_SCAFFOLD
from epiphany.runtime.providers.base import (
    ProviderResult,
    RetryableProviderError,
    TaskInvocation,
)


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
        segments = input_json["source_segments"]
        return {
            "timeline_events": [
                {
                    "label": f"Candidate moment {index}",
                    "description": "A deterministic timeline candidate from the cited segment.",
                    "time_expression": None,
                    "confidence": 0.8,
                    "source_refs": [
                        {
                            "source_id": segment["source_id"],
                            "source_segment_id": segment["source_segment_id"],
                        }
                    ],
                }
                for index, segment in enumerate(segments[:3], start=1)
            ],
            "open_questions": [],
        }

    @staticmethod
    def _theme_research(input_json: dict[str, Any]) -> dict[str, Any]:
        segment = input_json["source_segments"][0]
        quote = segment["text"][: min(len(segment["text"]), 160)]
        source_ref = {
            "source_id": segment["source_id"],
            "source_segment_id": segment["source_segment_id"],
        }
        return {
            "themes": [
                {
                    "theme": "Change outside the plan",
                    "insight": "A deterministic theme candidate grounded in the cited segment.",
                    "confidence": 0.85,
                    "source_refs": [source_ref],
                }
            ],
            "quotes": [
                {
                    "quote": quote,
                    "context": "Exact text retained for later human review.",
                    "source_ref": source_ref,
                }
            ],
        }

    @staticmethod
    def _build_interview_scaffold(input_json: dict[str, Any]) -> dict[str, Any]:
        timeline_event = input_json["timeline"]["timeline_events"][0]
        theme = input_json["themes"]["themes"][0]
        timeline_refs = timeline_event["source_refs"]
        theme_refs = theme["source_refs"]
        topic = input_json["topic"]
        return {
            "title": topic,
            "episode_intent": {
                "text": "从已有时间线与主题证据出发，补充具体经历与认知变化。",
                "source_refs": timeline_refs,
            },
            "opening": {
                "text": f"这一次，我们先围绕“{topic}”把已经出现的线索慢慢展开。",
                "source_refs": timeline_refs,
            },
            "sections": [
                {
                    "title": "回到事情发生的时刻",
                    "source_refs": timeline_refs,
                    "known_context": [
                        {
                            "text": timeline_event["description"],
                            "source_refs": timeline_refs,
                        }
                    ],
                    "transition": {
                        "text": "先不急着总结，我们回到这件事发生时的具体场景。",
                        "source_refs": timeline_refs,
                    },
                    "questions": [
                        {
                            "prompt": "如果把时间拉回那个时刻，你最先看见或感受到什么？",
                            "purpose": "补充已有时间线没有记录的场景与感官细节。",
                            "keywords": ["场景", "感受", "细节"],
                            "source_refs": timeline_refs,
                        }
                    ],
                },
                {
                    "title": "理解这段经历留下的变化",
                    "source_refs": theme_refs,
                    "known_context": [
                        {
                            "text": theme["insight"],
                            "source_refs": theme_refs,
                        }
                    ],
                    "transition": {
                        "text": "有了当时的画面，再看看这段经历后来如何改变了你。",
                        "source_refs": theme_refs,
                    },
                    "questions": [
                        {
                            "prompt": "现在回头看，你对这件事的理解和当时有什么不同？",
                            "purpose": "把已有主题继续追问到具体的认知变化。",
                            "keywords": ["回望", "变化", "理解"],
                            "source_refs": theme_refs,
                        }
                    ],
                },
            ],
            "material_gaps": [],
            "closing": {
                "text": "先从最有画面的部分开始讲，新的记忆出现时再顺着它继续追问。",
                "source_refs": theme_refs,
            },
        }
