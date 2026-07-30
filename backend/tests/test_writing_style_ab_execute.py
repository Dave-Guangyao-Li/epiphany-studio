from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from test_writing_style_ab import (
    INITIAL,
    STYLE,
    _draft,
    _frozen,
    _style_text,
)

from epiphany.draft_quality_schemas import (
    PERSONAL_STYLE_DIMENSION,
    expected_review_dimensions,
)
from epiphany.runtime.providers import ProviderResult, TaskInvocation
from epiphany.writing_style_ab import build_preflight
from epiphany.writing_style_ab_execute import (
    ARM_ORDER,
    WritingStyleABContractMismatch,
    WritingStyleABOutputExists,
    execute_writing_style_ab,
)

MAX_EDITOR_BUNDLE_CHARS = 50_000
MAX_EDITOR_TOKENS = 10_000
MAX_QUALITY_BUNDLE_CHARS = 80_000
MAX_QUALITY_TOKENS = 6_000


def _editor_draft(label: str) -> dict[str, Any]:
    draft = deepcopy(_draft())
    draft["podcast_script"]["opening"]["text"] = (  # type: ignore[index]
        f"{label}，前几天我重新点开了一段五年前的录音。"
    )
    return draft


def _review_for(invocation: TaskInvocation) -> dict[str, Any]:
    draft = invocation.input_json["podcast_draft"]
    opening = draft["podcast_script"]["opening"]
    style = invocation.input_json["writing_style_segments"][0]
    dimensions: list[dict[str, Any]] = []
    for dimension in expected_review_dimensions("ready"):
        card: dict[str, Any] = {
            "dimension": dimension,
            "assessable": True,
            "score": 4,
            "assessment": f"{dimension} 有可核对的候选稿证据。",
            "limitation": None,
            "evidence": [
                {
                    "location": "podcast_script.opening",
                    "exact_quote": opening["text"],
                    "source_refs": (
                        opening["source_refs"] if dimension == "source_faithfulness" else []
                    ),
                }
            ],
            "style_sample_evidence": [],
        }
        if dimension == PERSONAL_STYLE_DIMENSION:
            card["style_sample_evidence"] = [
                {
                    "location": "writing_style_segments[0]",
                    "exact_quote": style["text"][:20],
                    "source_ref": {
                        "source_id": style["source_id"],
                        "source_segment_id": style["source_segment_id"],
                    },
                }
            ]
        dimensions.append(card)
    return {
        "review_kind": "model_self_review",
        "advisory": True,
        "dimensions": dimensions,
    }


class ScriptedProvider:
    name = "deepseek"
    billing_currency = "CNY"

    def __init__(
        self,
        *,
        model: str,
        outputs: list[dict[str, Any]] | None = None,
    ) -> None:
        self.model = model
        self.outputs = list(outputs or [])
        self.invocations: list[TaskInvocation] = []

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        self.invocations.append(deepcopy(invocation))
        content = self.outputs.pop(0) if self.outputs else _review_for(invocation)
        return ProviderResult(
            content=content,
            provider=self.name,
            model=self.model,
            input_tokens=1_000 + len(self.invocations),
            output_tokens=500,
            estimated_cost_micros=2_000,
            cost_currency=self.billing_currency,
        )


def _contract_hash(*, database_path: Path, max_editor_tokens: int) -> str:
    preflight = build_preflight(
        frozen=_frozen(),
        max_editor_bundle_chars=MAX_EDITOR_BUNDLE_CHARS,
        max_editor_tokens=max_editor_tokens,
        max_quality_bundle_chars=MAX_QUALITY_BUNDLE_CHARS,
        max_quality_tokens=MAX_QUALITY_TOKENS,
        api_key_present=True,
        database_path=database_path,
        billing_currency="CNY",
    )
    return str(preflight["common_experiment_contract_sha256"])


async def _execute(
    *,
    tmp_path: Path,
    editor: ScriptedProvider,
    reviewer: ScriptedProvider,
    expected_contract_sha256: str,
    max_editor_tokens: int = MAX_EDITOR_TOKENS,
) -> dict[str, Any]:
    return await execute_writing_style_ab(
        frozen=_frozen(),
        editor_provider=editor,
        reviewer_provider=reviewer,
        expected_contract_sha256=expected_contract_sha256,
        output_dir=tmp_path / "results",
        database_path=tmp_path / "source.db",
        max_editor_bundle_chars=MAX_EDITOR_BUNDLE_CHARS,
        max_editor_tokens=max_editor_tokens,
        max_quality_bundle_chars=MAX_QUALITY_BUNDLE_CHARS,
        max_quality_tokens=MAX_QUALITY_TOKENS,
        editor_order=ARM_ORDER,
        reviewer_order=ARM_ORDER,
        billing_currency="CNY",
    )


async def test_success_uses_exactly_two_editors_and_two_shared_sample_reviewers(
    tmp_path: Path,
) -> None:
    editor = ScriptedProvider(
        model="deepseek-v4-flash",
        outputs=[_editor_draft("对照稿"), _editor_draft("样本稿")],
    )
    reviewer = ScriptedProvider(model="deepseek-v4-pro")
    manifest = await _execute(
        tmp_path=tmp_path,
        editor=editor,
        reviewer=reviewer,
        expected_contract_sha256=_contract_hash(
            database_path=tmp_path / "source.db",
            max_editor_tokens=MAX_EDITOR_TOKENS,
        ),
    )

    assert manifest["passed"] is True
    assert len(editor.invocations) == 2
    assert len(reviewer.invocations) == 2
    assert [call["phase"] for call in manifest["calls"]] == [
        "editor",
        "editor",
        "reviewer",
        "reviewer",
    ]
    first_editor, second_editor = editor.invocations
    assert first_editor.input_json["writing_style_profile"] is None
    assert first_editor.input_json["writing_style_segments"] is None
    assert second_editor.input_json["writing_style_profile"]["readiness"]["status"] == "ready"
    assert {
        key: value
        for key, value in first_editor.input_json.items()
        if key not in {"writing_style_profile", "writing_style_segments"}
    } == {
        key: value
        for key, value in second_editor.input_json.items()
        if key not in {"writing_style_profile", "writing_style_segments"}
    }
    assert (
        reviewer.invocations[0].input_json["writing_style_profile"]
        == reviewer.invocations[1].input_json["writing_style_profile"]
    )
    assert (
        reviewer.invocations[0].input_json["writing_style_segments"]
        == reviewer.invocations[1].input_json["writing_style_segments"]
    )
    assert {path.name for path in (tmp_path / "results").iterdir()} == {
        "manifest.json",
        "without-sample-draft.json",
        "without-sample-quality.json",
        "with-sample-draft.json",
        "with-sample-quality.json",
    }
    serialized_manifest = json.dumps(manifest, ensure_ascii=False)
    assert _style_text() not in serialized_manifest
    assert _frozen().editor_task_input.initial_source_segments[0].text not in serialized_manifest
    assert STYLE["source_id"] not in serialized_manifest
    assert manifest["privacy"] == {
        "manifest_contains_source_text": False,
        "manifest_contains_writing_sample_text": False,
        "manifest_contains_prompt_text": False,
        "manifest_contains_model_response_text": False,
        "manifest_contains_api_key": False,
        "private_draft_files_contain_model_response_text": True,
        "private_quality_files_may_contain_source_or_style_quotes": True,
        "private_files_mode": "0600",
        "output_directory_mode": "0700",
    }
    assert (tmp_path / "results").stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in (tmp_path / "results").iterdir())


async def test_contract_drift_blocks_before_any_provider_call(tmp_path: Path) -> None:
    editor = ScriptedProvider(
        model="deepseek-v4-flash",
        outputs=[_editor_draft("对照稿"), _editor_draft("样本稿")],
    )
    reviewer = ScriptedProvider(model="deepseek-v4-pro")
    expected = _contract_hash(
        database_path=tmp_path / "source.db",
        max_editor_tokens=MAX_EDITOR_TOKENS,
    )

    with pytest.raises(WritingStyleABContractMismatch):
        await _execute(
            tmp_path=tmp_path,
            editor=editor,
            reviewer=reviewer,
            expected_contract_sha256=expected,
            max_editor_tokens=MAX_EDITOR_TOKENS - 1,
        )

    assert editor.invocations == []
    assert reviewer.invocations == []
    assert not (tmp_path / "results").exists()


async def test_invalid_second_editor_stops_and_records_safe_accounting(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    editor = ScriptedProvider(
        model="deepseek-v4-flash",
        outputs=[_editor_draft("对照稿"), {"invalid": "private-model-output"}],
    )
    reviewer = ScriptedProvider(model="deepseek-v4-pro")
    api_key = "sk-private-test-key"

    manifest = await _execute(
        tmp_path=tmp_path,
        editor=editor,
        reviewer=reviewer,
        expected_contract_sha256=_contract_hash(
            database_path=tmp_path / "source.db",
            max_editor_tokens=MAX_EDITOR_TOKENS,
        ),
    )

    assert manifest["passed"] is False
    assert len(editor.invocations) == 2
    assert reviewer.invocations == []
    assert len(manifest["calls"]) == 2
    failure = manifest["calls"][-1]
    assert failure["status"] == "failed"
    assert failure["input_tokens"] == 1_002
    assert failure["output_tokens"] == 500
    assert failure["estimated_cost_micros"] == 2_000
    assert {path.name for path in (tmp_path / "results").iterdir()} == {"manifest.json"}
    safe_output = (tmp_path / "results" / "manifest.json").read_text(encoding="utf-8")
    assert "private-model-output" not in safe_output
    assert _style_text() not in safe_output
    assert api_key not in safe_output
    assert api_key not in caplog.text
    assert INITIAL["source_id"] not in caplog.text


async def test_reviewer_failure_stops_fourth_call_and_keeps_accounting(
    tmp_path: Path,
) -> None:
    editor = ScriptedProvider(
        model="deepseek-v4-flash",
        outputs=[_editor_draft("对照稿"), _editor_draft("样本稿")],
    )
    reviewer = ScriptedProvider(
        model="deepseek-v4-pro",
        outputs=[{"invalid": "review"}],
    )

    manifest = await _execute(
        tmp_path=tmp_path,
        editor=editor,
        reviewer=reviewer,
        expected_contract_sha256=_contract_hash(
            database_path=tmp_path / "source.db",
            max_editor_tokens=MAX_EDITOR_TOKENS,
        ),
    )

    assert manifest["passed"] is False
    assert len(editor.invocations) == 2
    assert len(reviewer.invocations) == 1
    assert [call["status"] for call in manifest["calls"]] == [
        "succeeded",
        "succeeded",
        "failed",
    ]
    assert manifest["protocol"]["successful_call_count"] == 2
    assert manifest["estimated_cost_micros_by_currency"] == {"CNY": 6_000}


async def test_existing_output_directory_blocks_before_provider_calls(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    editor = ScriptedProvider(
        model="deepseek-v4-flash",
        outputs=[_editor_draft("对照稿"), _editor_draft("样本稿")],
    )
    reviewer = ScriptedProvider(model="deepseek-v4-pro")

    with pytest.raises(WritingStyleABOutputExists):
        await _execute(
            tmp_path=tmp_path,
            editor=editor,
            reviewer=reviewer,
            expected_contract_sha256=_contract_hash(
                database_path=tmp_path / "source.db",
                max_editor_tokens=MAX_EDITOR_TOKENS,
            ),
        )

    assert editor.invocations == []
    assert reviewer.invocations == []
