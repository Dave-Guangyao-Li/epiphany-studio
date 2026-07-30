from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import secrets
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from epiphany.config import Settings
from epiphany.draft_quality import (
    analyze_podcast_draft,
    build_deterministic_quality_facts,
    build_draft_quality_report,
)
from epiphany.draft_quality_schemas import (
    REVIEW_PODCAST_DRAFT,
    STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION,
    STYLE_AWARE_MODEL_REVIEW_TASK_VERSION,
    ModelSelfReviewOutput,
    ModelSelfReviewTaskInput,
    validate_model_self_review_output,
)
from epiphany.editor_schemas import (
    BUILD_PODCAST_DRAFT,
    PodcastDraftOutput,
    editor_output_reference_keys,
    validate_podcast_draft_output,
)
from epiphany.observability import configure_logging
from epiphany.runtime.providers import (
    DeepSeekProvider,
    ModelProvider,
    ProviderResult,
    TaskInvocation,
)
from epiphany.writing_style_ab import (
    EDITOR_MODEL,
    EXPERIMENT_REQUEST_TIMEOUT_SECONDS,
    PLANNED_LIVE_CALL_COUNT,
    REVIEWER_MODEL,
    build_arm_inputs,
    build_preflight,
    database_url_for_path,
    load_frozen_input_from_run,
)
from epiphany.writing_style_ab_schemas import (
    FrozenWritingStyleABInput,
    WritingStyleABArm,
)

EXECUTION_RESULT_VERSION = "writing_style_ab_execution_v1"
DEFAULT_OUTPUT_DIR = Path("artifacts/m3-7b-writing-style-ab")
ARM_ORDER: tuple[WritingStyleABArm, ...] = ("without_sample", "with_sample")
ExecutionStatus = Literal["running", "succeeded", "failed"]

logger = logging.getLogger("epiphany.writing_style_ab")


class WritingStyleABExecutionError(RuntimeError):
    code = "writing_style_ab_execution_error"


class WritingStyleABContractMismatch(WritingStyleABExecutionError):
    code = "writing_style_ab_contract_mismatch"


class WritingStyleABProviderInvalid(WritingStyleABExecutionError):
    code = "writing_style_ab_provider_invalid"


class WritingStyleABOutputExists(WritingStyleABExecutionError):
    code = "writing_style_ab_output_exists"


class _CallFailed(WritingStyleABExecutionError):
    def __init__(self, *, record: dict[str, Any], cause: Exception) -> None:
        super().__init__("one bounded experiment call failed")
        self.record = record
        self.cause = cause


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _result_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "manifest": output_dir / "manifest.json",
        "without_sample_draft": output_dir / "without-sample-draft.json",
        "without_sample_quality": output_dir / "without-sample-quality.json",
        "with_sample_draft": output_dir / "with-sample-draft.json",
        "with_sample_quality": output_dir / "with-sample-quality.json",
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically persist a private experiment file.

    The temporary file lives beside the destination so ``os.replace`` stays
    atomic. Restrictive permissions matter because Draft and Reviewer files
    can contain personal Source text or short writing-sample quotes.
    """

    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _prepare_output_dir(paths: Mapping[str, Path], output_dir: Path) -> None:
    if output_dir.exists() or any(path.exists() for path in paths.values()):
        raise WritingStyleABOutputExists(
            "choose a new output directory; experiment results are never overwritten"
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_dir.mkdir(mode=0o700)
    except FileExistsError as error:
        raise WritingStyleABOutputExists(
            "another process already claimed this experiment output directory"
        ) from error
    os.chmod(output_dir, 0o700)


def _validated_arm_order(
    value: tuple[WritingStyleABArm, ...] | None,
) -> tuple[WritingStyleABArm, ...]:
    if value is None:
        return ARM_ORDER if secrets.randbelow(2) == 0 else tuple(reversed(ARM_ORDER))
    if len(value) != len(ARM_ORDER) or set(value) != set(ARM_ORDER):
        raise WritingStyleABExecutionError(
            "arm order must contain without_sample and with_sample exactly once"
        )
    return value


def _started_call_record(
    *,
    order: int,
    phase: Literal["editor", "reviewer"],
    arm: WritingStyleABArm,
    provider: ModelProvider,
) -> dict[str, Any]:
    """Leave durable evidence before a request may incur cost.

    If the process is killed during the network call, the manifest remains in
    ``started`` state. That does not prove whether billing happened, but it
    avoids incorrectly reporting zero calls.
    """

    return {
        "order": order,
        "phase": phase,
        "arm": arm,
        "status": "started",
        "provider": provider.name,
        "model": provider.model,
        "input_tokens": None,
        "output_tokens": None,
        "estimated_cost_micros": None,
        "cost_currency": None,
        "duration_ms": None,
        "error_code": None,
        "error_type": None,
    }


def _validate_providers(
    *,
    editor_provider: ModelProvider,
    reviewer_provider: ModelProvider,
    max_editor_bundle_chars: int,
    max_editor_tokens: int,
    max_quality_bundle_chars: int,
    max_quality_tokens: int,
    provider_base_url: str,
    billing_currency: str,
    request_timeout_seconds: float,
) -> None:
    if editor_provider.name != "deepseek" or editor_provider.model != EDITOR_MODEL:
        raise WritingStyleABProviderInvalid("A/B Editor must use the frozen DeepSeek Flash tier")
    if reviewer_provider.name != "deepseek" or reviewer_provider.model != REVIEWER_MODEL:
        raise WritingStyleABProviderInvalid("A/B Reviewer must use the frozen DeepSeek Pro tier")
    expected = {
        "base_url": provider_base_url.rstrip("/"),
        "billing_currency": billing_currency.upper(),
        "request_timeout_seconds": request_timeout_seconds,
        "editor_max_tokens": max_editor_tokens,
        "quality_review_max_tokens": max_quality_tokens,
        "max_editor_bundle_chars": max_editor_bundle_chars,
        "max_quality_bundle_chars": max_quality_bundle_chars,
    }
    for provider in (editor_provider, reviewer_provider):
        for attribute, expected_value in expected.items():
            if hasattr(provider, attribute) and getattr(provider, attribute) != expected_value:
                raise WritingStyleABProviderInvalid(
                    f"A/B Provider {attribute} differs from the frozen contract"
                )


def _accounting_result(
    provider_result: ProviderResult | None,
    error: Exception,
) -> ProviderResult | None:
    return provider_result or getattr(error, "accounting_result", None)


def _call_record(
    *,
    order: int,
    phase: Literal["editor", "reviewer"],
    arm: WritingStyleABArm,
    status: Literal["succeeded", "failed"],
    duration_ms: int,
    result: ProviderResult | None,
    error: Exception | None = None,
) -> dict[str, Any]:
    return {
        "order": order,
        "phase": phase,
        "arm": arm,
        "status": status,
        "provider": None if result is None else result.provider,
        "model": None if result is None else result.model,
        "input_tokens": None if result is None else result.input_tokens,
        "output_tokens": None if result is None else result.output_tokens,
        "estimated_cost_micros": (None if result is None else result.estimated_cost_micros),
        "cost_currency": None if result is None else result.cost_currency,
        "duration_ms": duration_ms,
        "error_code": None if error is None else getattr(error, "code", "call_failed"),
        "error_type": None if error is None else type(error).__name__,
    }


async def _invoke(
    *,
    provider: ModelProvider,
    invocation: TaskInvocation,
    order: int,
    phase: Literal["editor", "reviewer"],
    arm: WritingStyleABArm,
    validate: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    provider_result: ProviderResult | None = None
    started_at = perf_counter()
    try:
        provider_result = await provider.generate(invocation)
        if provider_result.provider != provider.name or provider_result.model != provider.model:
            raise WritingStyleABProviderInvalid(
                "provider result identity differs from the requested tier"
            )
        validated = validate(provider_result.content)
    except Exception as error:
        duration_ms = max(0, round((perf_counter() - started_at) * 1_000))
        accounting = _accounting_result(provider_result, error)
        record = _call_record(
            order=order,
            phase=phase,
            arm=arm,
            status="failed",
            duration_ms=duration_ms,
            result=accounting,
            error=error,
        )
        logger.error(
            "Writing style A/B call failed",
            extra={
                "event": "writing_style_ab.call.failed",
                "run_id": invocation.run_id,
                "task_id": invocation.task_id,
                "phase": phase,
                "arm": arm,
                "order": order,
                "error_code": record["error_code"],
                "error_type": record["error_type"],
            },
        )
        raise _CallFailed(record=record, cause=error) from error

    duration_ms = max(0, round((perf_counter() - started_at) * 1_000))
    record = _call_record(
        order=order,
        phase=phase,
        arm=arm,
        status="succeeded",
        duration_ms=duration_ms,
        result=provider_result,
    )
    logger.info(
        "Writing style A/B call completed",
        extra={
            "event": "writing_style_ab.call.completed",
            "run_id": invocation.run_id,
            "task_id": invocation.task_id,
            "phase": phase,
            "arm": arm,
            "order": order,
            "provider": provider_result.provider,
            "model": provider_result.model,
            "input_tokens": provider_result.input_tokens,
            "output_tokens": provider_result.output_tokens,
            "estimated_cost_micros": provider_result.estimated_cost_micros,
            "cost_currency": provider_result.cost_currency,
            "duration_ms": duration_ms,
        },
    )
    return validated, record


def _build_reviewer_input(
    *,
    frozen: FrozenWritingStyleABInput,
    arm: WritingStyleABArm,
    draft: PodcastDraftOutput,
    deterministic: Any,
) -> ModelSelfReviewTaskInput:
    editor_input = frozen.editor_task_input
    if (
        editor_input.creative_brief is None
        or editor_input.writing_style_profile is None
        or not editor_input.writing_style_segments
    ):
        raise WritingStyleABContractMismatch(
            "A/B Reviewer requires one shared ready writing sample"
        )

    allowed_keys = sorted(set(editor_output_reference_keys(draft.model_dump(mode="json"))))
    segments = [
        *editor_input.initial_source_segments,
        *editor_input.supplemental_source_segments,
    ]
    segments_by_key = {
        (segment.source_id, segment.source_segment_id): segment for segment in segments
    }
    if not set(allowed_keys) <= set(segments_by_key):
        raise WritingStyleABContractMismatch(
            "A/B Draft references unavailable factual source material"
        )
    return ModelSelfReviewTaskInput.model_validate(
        {
            "review_contract_version": STYLE_AWARE_MODEL_REVIEW_TASK_VERSION,
            "task_kind": REVIEW_PODCAST_DRAFT,
            "draft_artifact_id": f"m37b-{arm}-draft",
            "deterministic_metrics_artifact_id": f"m37b-{arm}-metrics",
            "deterministic_quality_facts": build_deterministic_quality_facts(
                deterministic
            ).model_dump(mode="json"),
            "creative_brief": editor_input.creative_brief.model_dump(mode="json"),
            "quality_config": frozen.quality_config.model_dump(mode="json"),
            "podcast_draft": draft.model_dump(mode="json"),
            "allowed_source_refs": [
                {
                    "source_id": source_id,
                    "source_segment_id": segment_id,
                }
                for source_id, segment_id in allowed_keys
            ],
            "referenced_source_segments": [
                {
                    "source_id": source_id,
                    "source_segment_id": segment_id,
                    "text": segments_by_key[(source_id, segment_id)].text,
                }
                for source_id, segment_id in allowed_keys
            ],
            # Both Reviewers receive the same ready sample. Only the Editor
            # treatment differs, so personal_style_match remains comparable.
            "writing_style_profile": editor_input.writing_style_profile.model_dump(mode="json"),
            "writing_style_segments": [
                segment.model_dump(mode="json") for segment in editor_input.writing_style_segments
            ],
        }
    )


def _safe_manifest(
    *,
    frozen: FrozenWritingStyleABInput,
    expected_contract_sha256: str,
    actual_contract_sha256: str,
    calls: list[dict[str, Any]],
    status: ExecutionStatus,
    paths: Mapping[str, Path],
    editor_order: tuple[WritingStyleABArm, ...],
    reviewer_order: tuple[WritingStyleABArm, ...],
    arm_summaries: Mapping[str, Any] | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    costs: dict[str, int] = {}
    for call in calls:
        currency = call.get("cost_currency")
        cost = call.get("estimated_cost_micros")
        if isinstance(currency, str) and isinstance(cost, int):
            costs[currency] = costs.get(currency, 0) + cost
    return {
        "schema_version": EXECUTION_RESULT_VERSION,
        "event": {
            "running": "writing_style_ab.execution.running",
            "succeeded": "writing_style_ab.execution.completed",
            "failed": "writing_style_ab.execution.blocked",
        }[status],
        "status": status,
        "passed": status == "succeeded",
        "source_run_id": frozen.source_run_id,
        "expected_contract_sha256": expected_contract_sha256,
        "actual_contract_sha256": actual_contract_sha256,
        "contract_hash_matched": expected_contract_sha256 == actual_contract_sha256,
        "protocol": {
            "editor_order": list(editor_order),
            "reviewer_order": list(reviewer_order),
            "call_order": [
                *(f"editor:{arm}" for arm in editor_order),
                *(f"reviewer:{arm}" for arm in reviewer_order),
            ],
            "editor_model": EDITOR_MODEL,
            "reviewer_model": REVIEWER_MODEL,
            "retry_enabled": False,
            "arm_order_randomized_by_default": True,
            "successful_call_count_required": PLANNED_LIVE_CALL_COUNT,
            "provider_call_count": len(calls),
            "successful_call_count": sum(call.get("status") == "succeeded" for call in calls),
            "started_call_means_billing_unknown_after_crash": True,
            "reviewers_share_ready_sample": True,
        },
        "calls": calls,
        "estimated_cost_micros_by_currency": costs,
        "arms": dict(arm_summaries or {}),
        "output_files": {
            name: str(path)
            for name, path in paths.items()
            if name == "manifest" or status == "succeeded"
        },
        "failure": (
            None
            if error is None
            else {
                "error_code": getattr(error, "code", "writing_style_ab_call_failed"),
                "error_type": type(error).__name__,
            }
        ),
        "privacy": {
            "manifest_contains_source_text": False,
            "manifest_contains_writing_sample_text": False,
            "manifest_contains_prompt_text": False,
            "manifest_contains_model_response_text": False,
            "manifest_contains_api_key": False,
            "private_draft_files_contain_model_response_text": True,
            "private_quality_files_may_contain_source_or_style_quotes": True,
            "private_files_mode": "0600",
            "output_directory_mode": "0700",
        },
    }


async def execute_writing_style_ab(
    *,
    frozen: FrozenWritingStyleABInput,
    editor_provider: ModelProvider,
    reviewer_provider: ModelProvider,
    expected_contract_sha256: str,
    output_dir: Path,
    database_path: Path,
    max_editor_bundle_chars: int,
    max_editor_tokens: int,
    max_quality_bundle_chars: int,
    max_quality_tokens: int,
    editor_order: tuple[WritingStyleABArm, ...] | None = None,
    reviewer_order: tuple[WritingStyleABArm, ...] | None = None,
    provider_base_url: str = "https://api.deepseek.com",
    billing_currency: str = "USD",
    request_timeout_seconds: float = EXPERIMENT_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute one bounded pair without mutating the source Run or database."""

    _validate_providers(
        editor_provider=editor_provider,
        reviewer_provider=reviewer_provider,
        max_editor_bundle_chars=max_editor_bundle_chars,
        max_editor_tokens=max_editor_tokens,
        max_quality_bundle_chars=max_quality_bundle_chars,
        max_quality_tokens=max_quality_tokens,
        provider_base_url=provider_base_url,
        billing_currency=billing_currency,
        request_timeout_seconds=request_timeout_seconds,
    )
    preflight = build_preflight(
        frozen=frozen,
        max_editor_bundle_chars=max_editor_bundle_chars,
        max_editor_tokens=max_editor_tokens,
        max_quality_bundle_chars=max_quality_bundle_chars,
        max_quality_tokens=max_quality_tokens,
        api_key_present=True,
        database_path=database_path,
        provider_base_url=provider_base_url,
        billing_currency=billing_currency,
        request_timeout_seconds=request_timeout_seconds,
    )
    actual_contract_sha256 = str(preflight["common_experiment_contract_sha256"])
    if expected_contract_sha256 != actual_contract_sha256:
        raise WritingStyleABContractMismatch(
            "execution contract differs from the confirmed dry-run contract"
        )

    paths = _result_paths(output_dir)
    await asyncio.to_thread(_prepare_output_dir, paths, output_dir)
    actual_editor_order = _validated_arm_order(editor_order)
    actual_reviewer_order = _validated_arm_order(reviewer_order)

    logger.info(
        "Writing style A/B execution started",
        extra={
            "event": "writing_style_ab.execution.started",
            "run_id": frozen.source_run_id,
            "contract_sha256": actual_contract_sha256,
            "planned_call_count": PLANNED_LIVE_CALL_COUNT,
        },
    )
    calls: list[dict[str, Any]] = []
    drafts: dict[WritingStyleABArm, PodcastDraftOutput] = {}
    deterministic_results: dict[WritingStyleABArm, Any] = {}
    reviews: dict[WritingStyleABArm, ModelSelfReviewOutput] = {}
    reports: dict[WritingStyleABArm, Any] = {}
    arms = build_arm_inputs(frozen)

    def manifest_for(
        status: ExecutionStatus,
        *,
        arm_summaries: Mapping[str, Any] | None = None,
        error: Exception | None = None,
    ) -> dict[str, Any]:
        return _safe_manifest(
            frozen=frozen,
            expected_contract_sha256=expected_contract_sha256,
            actual_contract_sha256=actual_contract_sha256,
            calls=calls,
            status=status,
            paths=paths,
            editor_order=actual_editor_order,
            reviewer_order=actual_reviewer_order,
            arm_summaries=arm_summaries,
            error=error,
        )

    # Persist the experiment claim before any potentially billable request.
    await asyncio.to_thread(_write_json, paths["manifest"], manifest_for("running"))

    try:
        for order, arm in enumerate(actual_editor_order, start=1):
            task_input = arms[arm].model_dump(mode="json")
            calls.append(
                _started_call_record(
                    order=order,
                    phase="editor",
                    arm=arm,
                    provider=editor_provider,
                )
            )
            await asyncio.to_thread(
                _write_json,
                paths["manifest"],
                manifest_for("running"),
            )
            validated, record = await _invoke(
                provider=editor_provider,
                invocation=TaskInvocation(
                    task_id=f"m37b-editor-{arm}",
                    run_id=frozen.source_run_id,
                    kind=BUILD_PODCAST_DRAFT,
                    attempt=1,
                    input_json=task_input,
                    lease_token="m37b_single_attempt",
                ),
                order=order,
                phase="editor",
                arm=arm,
                validate=lambda content, task_input=task_input: validate_podcast_draft_output(
                    task_input=task_input,
                    content=content,
                ),
            )
            calls[-1] = record
            await asyncio.to_thread(
                _write_json,
                paths["manifest"],
                manifest_for("running"),
            )
            draft = PodcastDraftOutput.model_validate(validated)
            drafts[arm] = draft
            deterministic_results[arm] = analyze_podcast_draft(
                draft=draft,
                creative_brief=arms[arm].creative_brief,
                config=frozen.quality_config,
            )

        reviewer_inputs = {
            arm: _build_reviewer_input(
                frozen=frozen,
                arm=arm,
                draft=drafts[arm],
                deterministic=deterministic_results[arm],
            )
            for arm in ARM_ORDER
        }
        reviewer_style_hashes = {
            arm: _sha256(
                {
                    "writing_style_profile": reviewer_inputs[arm].writing_style_profile.model_dump(
                        mode="json"
                    ),
                    "writing_style_segments": [
                        segment.model_dump(mode="json")
                        for segment in reviewer_inputs[arm].writing_style_segments
                    ],
                }
            )
            for arm in ARM_ORDER
        }
        if len(set(reviewer_style_hashes.values())) != 1:
            raise WritingStyleABContractMismatch(
                "Reviewers did not receive the same ready writing sample"
            )

        for order, arm in enumerate(actual_reviewer_order, start=3):
            task_input = reviewer_inputs[arm].model_dump(mode="json")
            calls.append(
                _started_call_record(
                    order=order,
                    phase="reviewer",
                    arm=arm,
                    provider=reviewer_provider,
                )
            )
            await asyncio.to_thread(
                _write_json,
                paths["manifest"],
                manifest_for("running"),
            )
            validated, record = await _invoke(
                provider=reviewer_provider,
                invocation=TaskInvocation(
                    task_id=f"m37b-reviewer-{arm}",
                    run_id=frozen.source_run_id,
                    kind=REVIEW_PODCAST_DRAFT,
                    attempt=1,
                    input_json=task_input,
                    lease_token="m37b_single_attempt",
                ),
                order=order,
                phase="reviewer",
                arm=arm,
                validate=lambda content, task_input=task_input: validate_model_self_review_output(
                    task_input=task_input,
                    content=content,
                ),
            )
            calls[-1] = record
            await asyncio.to_thread(
                _write_json,
                paths["manifest"],
                manifest_for("running"),
            )
            review = ModelSelfReviewOutput.model_validate(validated)
            reviews[arm] = review
            reports[arm] = build_draft_quality_report(
                deterministic=deterministic_results[arm],
                model_self_review=review,
                editor_provider=editor_provider.name,
                editor_model=editor_provider.model,
                reviewer_provider=reviewer_provider.name,
                reviewer_model=reviewer_provider.model,
                scoring_formula_version=STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION,
                writing_style_context_status="ready",
            )
    except _CallFailed as error:
        calls[-1] = error.record
        manifest = manifest_for("failed", error=error.cause)
        await asyncio.to_thread(_write_json, paths["manifest"], manifest)
        return manifest
    except Exception as error:
        manifest = manifest_for("failed", error=error)
        await asyncio.to_thread(_write_json, paths["manifest"], manifest)
        return manifest

    arm_summaries: dict[str, Any] = {}
    for arm in ARM_ORDER:
        draft_payload = drafts[arm].model_dump(mode="json")
        quality_payload = {
            "schema_version": EXECUTION_RESULT_VERSION,
            "arm": arm,
            "deterministic": deterministic_results[arm].model_dump(mode="json"),
            "model_review": reviews[arm].model_dump(mode="json"),
            "report": reports[arm].model_dump(mode="json"),
        }
        await asyncio.to_thread(_write_json, paths[f"{arm}_draft"], draft_payload)
        await asyncio.to_thread(_write_json, paths[f"{arm}_quality"], quality_payload)
        arm_summaries[arm] = {
            "draft_sha256": _sha256(draft_payload),
            "quality_sha256": _sha256(quality_payload),
            "deterministic_score": deterministic_results[arm].deterministic_score,
            "estimated_duration_minutes": (
                deterministic_results[arm].metrics.estimated_duration_minutes
            ),
            "model_score": reports[arm].experimental_model_score,
            "overall_score": reports[arm].experimental_overall_score,
            "decision": reports[arm].decision,
        }

    manifest = manifest_for(
        (
            "succeeded"
            if len(calls) == PLANNED_LIVE_CALL_COUNT
            and all(call["status"] == "succeeded" for call in calls)
            else "failed"
        ),
        arm_summaries=arm_summaries,
    )
    await asyncio.to_thread(_write_json, paths["manifest"], manifest)
    logger.info(
        "Writing style A/B execution completed",
        extra={
            "event": "writing_style_ab.execution.completed",
            "run_id": frozen.source_run_id,
            "contract_sha256": actual_contract_sha256,
            "provider_call_count": len(calls),
        },
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate and review one controlled writing-sample A/B pair. "
            "Without --execute this command only prints the zero-network preflight."
        )
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--database", type=Path, default=Path("data/epiphany.db"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-contract-sha256")
    return parser


def _print_json(value: Mapping[str, Any], *, stream: Any | None = None) -> None:
    print(
        json.dumps(value, ensure_ascii=False, indent=2),
        file=sys.stdout if stream is None else stream,
        flush=True,
    )


def _build_live_providers(
    *,
    settings: Settings,
    api_key: str,
) -> tuple[ModelProvider, ModelProvider]:
    common = {
        "api_key": api_key,
        "billing_currency": settings.deepseek_billing_currency,
        "base_url": settings.deepseek_base_url,
        "editor_max_tokens": settings.deepseek_editor_max_tokens,
        "quality_review_max_tokens": settings.deepseek_quality_review_max_tokens,
        "max_editor_bundle_chars": settings.deepseek_max_editor_bundle_chars,
        "max_quality_bundle_chars": settings.deepseek_max_quality_bundle_chars,
        "request_timeout_seconds": EXPERIMENT_REQUEST_TIMEOUT_SECONDS,
    }
    return (
        DeepSeekProvider(model=EDITOR_MODEL, **common),
        DeepSeekProvider(model=REVIEWER_MODEL, **common),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    backend_dir = Path(__file__).resolve().parents[2]
    settings = Settings(_env_file=backend_dir / ".env")
    api_key = (
        settings.deepseek_api_key.get_secret_value().strip()
        if settings.deepseek_api_key is not None
        else ""
    )
    database_path = args.database.expanduser().resolve()
    try:
        frozen = asyncio.run(
            load_frozen_input_from_run(
                database_url=database_url_for_path(database_path),
                run_id=args.run_id,
            )
        )
        preflight = build_preflight(
            frozen=frozen,
            max_editor_bundle_chars=settings.deepseek_max_editor_bundle_chars,
            max_editor_tokens=settings.deepseek_editor_max_tokens,
            max_quality_bundle_chars=settings.deepseek_max_quality_bundle_chars,
            max_quality_tokens=settings.deepseek_quality_review_max_tokens,
            api_key_present=bool(api_key),
            database_path=database_path,
            provider_base_url=settings.deepseek_base_url,
            billing_currency=settings.deepseek_billing_currency,
            request_timeout_seconds=EXPERIMENT_REQUEST_TIMEOUT_SECONDS,
        )
        _print_json(preflight)
        if not args.execute:
            return 0
        if not args.expected_contract_sha256:
            raise WritingStyleABContractMismatch(
                "--execute requires --expected-contract-sha256 from this dry run"
            )
        if not api_key:
            raise WritingStyleABExecutionError(
                "EPIPHANY_DEEPSEEK_API_KEY is required for --execute"
            )
        configure_logging(settings.log_level)
        editor_provider, reviewer_provider = _build_live_providers(
            settings=settings,
            api_key=api_key,
        )
        manifest = asyncio.run(
            execute_writing_style_ab(
                frozen=frozen,
                editor_provider=editor_provider,
                reviewer_provider=reviewer_provider,
                expected_contract_sha256=args.expected_contract_sha256,
                output_dir=args.output_dir.expanduser().resolve(),
                database_path=database_path,
                max_editor_bundle_chars=settings.deepseek_max_editor_bundle_chars,
                max_editor_tokens=settings.deepseek_editor_max_tokens,
                max_quality_bundle_chars=settings.deepseek_max_quality_bundle_chars,
                max_quality_tokens=settings.deepseek_quality_review_max_tokens,
                provider_base_url=settings.deepseek_base_url,
                billing_currency=settings.deepseek_billing_currency,
                request_timeout_seconds=EXPERIMENT_REQUEST_TIMEOUT_SECONDS,
            )
        )
    except Exception as error:
        _print_json(
            {
                "event": "writing_style_ab.execution.blocked",
                "error_code": getattr(
                    error,
                    "code",
                    "writing_style_ab_execution_error",
                ),
                "error_type": type(error).__name__,
                "message": "The bounded writing-style A/B did not execute.",
            },
            stream=sys.stderr,
        )
        return 2
    _print_json(manifest)
    return 0 if manifest["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
