from __future__ import annotations

from typing import Any

from epiphany.runtime.providers.base import (
    ProviderResult,
    RetryableProviderError,
    TaskInvocation,
)


class FakeProvider:
    """Deterministic provider used to exercise runtime behavior without an API key."""

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
        }
        try:
            content = handlers[invocation.kind](invocation.input_json)
        except KeyError as error:
            raise ValueError(f"unsupported fake task kind: {invocation.kind}") from error

        return ProviderResult(content=content, provider="fake", model="fake-v1")

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
