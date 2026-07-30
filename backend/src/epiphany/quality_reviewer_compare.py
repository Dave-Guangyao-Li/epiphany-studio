from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from epiphany.config import Settings
from epiphany.db import Database
from epiphany.draft_quality import (
    analyze_podcast_draft,
    build_deterministic_quality_facts,
    build_draft_quality_report,
)
from epiphany.draft_quality_schemas import (
    CHINESE_STYLE_HEURISTIC_VERSION,
    DETERMINISTIC_QUALITY_FACTS_VERSION,
    DRAFT_QUALITY_FORMULA_VERSION,
    DRAFT_QUALITY_RULES_VERSION,
    REVIEW_DIMENSIONS,
    REVIEW_PODCAST_DRAFT,
    DeterministicDraftQualityResult,
    ModelSelfReviewOutput,
    ModelSelfReviewTaskInput,
    validate_model_self_review_output,
)
from epiphany.models import Artifact, Run, Task
from epiphany.observability import configure_logging
from epiphany.runtime.providers import (
    DeepSeekProvider,
    ModelProvider,
    TaskInvocation,
)
from epiphany.runtime.quality_prompts import build_quality_review_prompt

COMPARISON_INPUT_SCHEMA_VERSION = "quality_reviewer_comparison_input_v1"
COMPARISON_RESULT_SCHEMA_VERSION = "quality_reviewer_comparison_result_v1"
MODEL_REVIEW_SCHEMA_VERSION = "model_self_review_output_v1"
TRUSTED_REVIEWER_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")
DEFAULT_OUTPUT_PATH = Path("artifacts/m3-5-reviewer-comparison.json")
DEFAULT_REQUEST_TIMEOUT_SECONDS = 180.0


class ReviewerComparisonError(RuntimeError):
    code = "quality_reviewer_comparison_error"


class ComparisonInputInvalid(ReviewerComparisonError):
    code = "comparison_input_invalid"


class ComparisonRunNotFound(ReviewerComparisonError):
    code = "comparison_run_not_found"


class ComparisonArtifactsInvalid(ReviewerComparisonError):
    code = "comparison_artifacts_invalid"


class ComparisonProviderInvalid(ReviewerComparisonError):
    code = "comparison_provider_invalid"


class EditorExecutionIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)


class FrozenReviewerComparisonInput(BaseModel):
    """One normalized, immutable Reviewer input shared by both model tiers."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["quality_reviewer_comparison_input_v1"] = (
        COMPARISON_INPUT_SCHEMA_VERSION
    )
    task_input: ModelSelfReviewTaskInput
    deterministic_result: DeterministicDraftQualityResult
    editor_execution: EditorExecutionIdentity
    source_run_id: str | None = Field(default=None, min_length=1, max_length=64)
    deterministic_origin: Literal[
        "persisted_artifact",
        "recomputed_current_rules",
    ] = "persisted_artifact"

    @model_validator(mode="after")
    def facts_must_match_persisted_result(self) -> FrozenReviewerComparisonInput:
        rebuilt = build_deterministic_quality_facts(self.deterministic_result)
        if rebuilt != self.task_input.deterministic_quality_facts:
            raise ValueError("task deterministic_quality_facts must match deterministic_result")
        return self


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def frozen_input_sha256(bundle: FrozenReviewerComparisonInput) -> str:
    """Hash only the normalized task input used by both Reviewer calls."""

    serialized = _canonical_json(bundle.task_input.model_dump(mode="json"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def deterministic_result_sha256(bundle: FrozenReviewerComparisonInput) -> str:
    serialized = _canonical_json(bundle.deterministic_result.model_dump(mode="json"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def comparison_bundle_sha256(bundle: FrozenReviewerComparisonInput) -> str:
    serialized = _canonical_json(bundle.model_dump(mode="json"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def prompt_sha256(bundle: FrozenReviewerComparisonInput, *, max_bundle_chars: int) -> str:
    prompt = build_quality_review_prompt(
        task_input=bundle.task_input.model_dump(mode="json"),
        max_bundle_chars=max_bundle_chars,
    )
    serialized = _canonical_json(prompt.messages)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_comparison_input_file(path: Path) -> FrozenReviewerComparisonInput:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return FrozenReviewerComparisonInput.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError, TypeError) as error:
        raise ComparisonInputInvalid(
            "comparison input file did not match the strict frozen-input schema"
        ) from error


async def load_comparison_input_from_run(
    *,
    database_url: str,
    run_id: str,
    recompute_current_rules: bool = False,
) -> FrozenReviewerComparisonInput:
    """Load and cross-check a persisted current-contract Reviewer task."""

    database = Database(database_url)
    try:
        async with database.sessions() as session:
            run = await session.get(Run, run_id)
            if run is None:
                raise ComparisonRunNotFound("comparison source Run does not exist")

            reviewer_tasks = list(
                (
                    await session.execute(
                        select(Task)
                        .where(
                            Task.run_id == run_id,
                            Task.kind == REVIEW_PODCAST_DRAFT,
                        )
                        .order_by(Task.created_at, Task.id)
                    )
                )
                .scalars()
                .all()
            )
            if len(reviewer_tasks) != 1:
                raise ComparisonArtifactsInvalid(
                    "comparison source Run must contain exactly one Reviewer task"
                )
            task = reviewer_tasks[0]
            try:
                task_input = ModelSelfReviewTaskInput.model_validate(task.input_json)
            except (ValidationError, ValueError, TypeError) as error:
                raise ComparisonArtifactsInvalid(
                    "persisted Reviewer task input is invalid"
                ) from error
            if (
                task_input.deterministic_metrics_artifact_id is None
                or task_input.deterministic_quality_facts is None
            ):
                raise ComparisonArtifactsInvalid(
                    "comparison requires a Reviewer task with persisted deterministic facts"
                )

            draft_artifact = await session.get(Artifact, task_input.draft_artifact_id)
            metrics_artifact = await session.get(
                Artifact,
                task_input.deterministic_metrics_artifact_id,
            )
            if (
                draft_artifact is None
                or metrics_artifact is None
                or draft_artifact.run_id != run_id
                or metrics_artifact.run_id != run_id
                or draft_artifact.kind != "build_podcast_draft_result"
                or metrics_artifact.kind != "draft_metrics_report"
            ):
                raise ComparisonArtifactsInvalid(
                    "persisted Reviewer task does not reference valid same-Run Artifacts"
                )

            draft_content = {
                key: value
                for key, value in draft_artifact.content_json.items()
                if key != "_execution"
            }
            if task_input.podcast_draft.model_dump(mode="json") != type(
                task_input.podcast_draft
            ).model_validate(draft_content).model_dump(mode="json"):
                raise ComparisonArtifactsInvalid(
                    "persisted Draft Artifact differs from the frozen Reviewer task"
                )
            execution = draft_artifact.content_json.get("_execution")
            if not isinstance(execution, dict):
                raise ComparisonArtifactsInvalid(
                    "persisted Draft Artifact has no editor execution identity"
                )
            try:
                persisted_deterministic = DeterministicDraftQualityResult.model_validate(
                    metrics_artifact.content_json
                )
                deterministic = (
                    analyze_podcast_draft(
                        draft=task_input.podcast_draft,
                        creative_brief=task_input.creative_brief,
                        config=task_input.quality_config,
                    )
                    if recompute_current_rules
                    else persisted_deterministic
                )
                if recompute_current_rules:
                    task_input = task_input.model_copy(
                        update={
                            "deterministic_quality_facts": (
                                build_deterministic_quality_facts(deterministic)
                            )
                        }
                    )
                bundle = FrozenReviewerComparisonInput(
                    task_input=task_input,
                    deterministic_result=deterministic,
                    editor_execution=EditorExecutionIdentity.model_validate(
                        {
                            "provider": execution.get("provider"),
                            "model": execution.get("model"),
                        }
                    ),
                    source_run_id=run_id,
                    deterministic_origin=(
                        "recomputed_current_rules"
                        if recompute_current_rules
                        else "persisted_artifact"
                    ),
                )
            except (ValidationError, ValueError, TypeError) as error:
                raise ComparisonArtifactsInvalid(
                    "persisted comparison Artifacts are internally inconsistent"
                ) from error
            return bundle
    finally:
        await database.close()


def database_url_for_path(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.expanduser().resolve()}"


def build_preflight(
    *,
    execute: bool,
    api_key_present: bool,
    input_origin: str,
    frozen_sha256: str,
    deterministic_sha256: str,
    bundle_sha256: str,
    prompt_hash: str,
    output_path: str,
    billing_currency: str,
    deterministic_origin: str = "persisted_artifact",
) -> dict[str, Any]:
    """Return a safe plan containing hashes and counts, never source text."""

    return {
        "event": "quality_reviewer_compare.preflight",
        "mode": "execute" if execute else "dry-run",
        "execute_requested": execute,
        "network_enabled": execute and api_key_present,
        "paid_api_call_possible": execute and api_key_present,
        "api_key_status": "present" if api_key_present else "absent",
        "input_origin": input_origin,
        "deterministic_origin": deterministic_origin,
        "frozen_input_sha256": frozen_sha256,
        "deterministic_result_sha256": deterministic_sha256,
        "comparison_bundle_sha256": bundle_sha256,
        "prompt_sha256": prompt_hash,
        "models": list(TRUSTED_REVIEWER_MODELS),
        "model_call_count": len(TRUSTED_REVIEWER_MODELS),
        "same_frozen_input_for_every_call": True,
        "regenerates_podcast_draft": False,
        "billing_currency": billing_currency,
        "output_path": output_path,
    }


def _validate_providers(
    providers: Mapping[str, ModelProvider],
) -> None:
    if set(providers) != set(TRUSTED_REVIEWER_MODELS):
        raise ComparisonProviderInvalid("comparison requires exactly DeepSeek V4 Flash and Pro")
    for expected_model in TRUSTED_REVIEWER_MODELS:
        provider = providers[expected_model]
        if provider.name != "deepseek" or provider.model != expected_model:
            raise ComparisonProviderInvalid(
                "comparison providers must use the trusted DeepSeek model tiers"
            )


def _raw_dimension_scores(review: ModelSelfReviewOutput) -> dict[str, int | None]:
    cards = {card.dimension: card for card in review.dimensions}
    return {dimension: cards[dimension].score for dimension in REVIEW_DIMENSIONS}


def _safe_schema_issues(error: Exception) -> list[dict[str, str]]:
    """Expose only schema paths and error types, never model text or fixture data."""

    cause = error.__cause__
    if not isinstance(cause, ValidationError):
        return []
    return [
        {
            "path": ".".join(str(part) for part in issue["loc"]),
            "type": str(issue["type"]),
        }
        for issue in cause.errors(include_input=False, include_context=False)[:10]
    ]


async def compare_reviewers(
    *,
    bundle: FrozenReviewerComparisonInput,
    providers: Mapping[str, ModelProvider],
    max_bundle_chars: int,
) -> dict[str, Any]:
    """Review one frozen Draft twice without invoking an Editor."""

    _validate_providers(providers)
    task_input_json = bundle.task_input.model_dump(mode="json")
    input_hash = frozen_input_sha256(bundle)
    shared_prompt = build_quality_review_prompt(
        task_input=task_input_json,
        max_bundle_chars=max_bundle_chars,
    )
    shared_prompt_hash = hashlib.sha256(
        _canonical_json(shared_prompt.messages).encode("utf-8")
    ).hexdigest()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for order, model in enumerate(TRUSTED_REVIEWER_MODELS, start=1):
        provider = providers[model]
        provider_result = None
        invocation = TaskInvocation(
            task_id=f"comparison_{model}",
            run_id=bundle.source_run_id or f"comparison_{input_hash[:16]}",
            kind=REVIEW_PODCAST_DRAFT,
            attempt=1,
            input_json=task_input_json,
            lease_token="comparison_read_only",
        )
        started_at = perf_counter()
        try:
            provider_result = await provider.generate(invocation)
            duration_ms = max(0, round((perf_counter() - started_at) * 1_000))
            if provider_result.provider != "deepseek" or provider_result.model != model:
                raise ComparisonProviderInvalid(
                    "Reviewer result identity differs from the requested model tier"
                )
            validated = validate_model_self_review_output(
                task_input=task_input_json,
                content=provider_result.content,
            )
            review = ModelSelfReviewOutput.model_validate(validated)
            report = build_draft_quality_report(
                deterministic=bundle.deterministic_result,
                model_self_review=review,
                editor_provider=bundle.editor_execution.provider,
                editor_model=bundle.editor_execution.model,
                reviewer_provider=provider_result.provider,
                reviewer_model=provider_result.model,
            )
        except Exception as error:
            duration_ms = max(0, round((perf_counter() - started_at) * 1_000))
            accounting_result = provider_result or getattr(error, "accounting_result", None)
            failures.append(
                {
                    "model": model,
                    "error_code": getattr(error, "code", "comparison_model_call_failed"),
                    "error_type": type(error).__name__,
                    "duration_ms": duration_ms,
                    "schema_issues": _safe_schema_issues(error),
                    "input_tokens": (
                        None if accounting_result is None else accounting_result.input_tokens
                    ),
                    "output_tokens": (
                        None if accounting_result is None else accounting_result.output_tokens
                    ),
                    "estimated_cost_micros": (
                        None
                        if accounting_result is None
                        else accounting_result.estimated_cost_micros
                    ),
                    "cost_currency": (
                        None if accounting_result is None else accounting_result.cost_currency
                    ),
                }
            )
            continue

        results.append(
            {
                "order": order,
                "provider": provider_result.provider,
                "model": provider_result.model,
                "input_sha256": input_hash,
                "prompt_sha256": shared_prompt_hash,
                "input_tokens": provider_result.input_tokens,
                "output_tokens": provider_result.output_tokens,
                "duration_ms": duration_ms,
                "estimated_cost_micros": provider_result.estimated_cost_micros,
                "cost_currency": provider_result.cost_currency,
                "raw_dimension_scores": _raw_dimension_scores(review),
                "experimental_model_score": report.experimental_model_score,
                "experimental_uncapped_overall_score": (report.experimental_uncapped_overall_score),
                "code_owned_score_cap": report.code_owned_score_cap,
                "experimental_overall_score": report.experimental_overall_score,
                "decision": report.decision,
                "reviewer_relation": report.reviewer_relation,
                "score_cap_reason_codes": [reason.code for reason in report.score_cap_reasons],
                "conflict_codes": [conflict.code for conflict in report.model_review_conflicts],
            }
        )

    result_by_model = {result["model"]: result for result in results}
    flash = result_by_model.get("deepseek-v4-flash")
    pro = result_by_model.get("deepseek-v4-pro")
    comparison: dict[str, Any] | None = None
    if flash is not None and pro is not None:
        flash_model_score = flash["experimental_model_score"]
        pro_model_score = pro["experimental_model_score"]
        flash_overall_score = flash["experimental_overall_score"]
        pro_overall_score = pro["experimental_overall_score"]
        comparison = {
            "raw_dimension_score_delta_pro_minus_flash": {
                dimension: (
                    None
                    if flash["raw_dimension_scores"][dimension] is None
                    or pro["raw_dimension_scores"][dimension] is None
                    else (
                        pro["raw_dimension_scores"][dimension]
                        - flash["raw_dimension_scores"][dimension]
                    )
                )
                for dimension in REVIEW_DIMENSIONS
            },
            "model_score_delta_pro_minus_flash": (
                None
                if flash_model_score is None or pro_model_score is None
                else round(pro_model_score - flash_model_score, 2)
            ),
            "overall_score_delta_pro_minus_flash": (
                None
                if flash_overall_score is None or pro_overall_score is None
                else round(pro_overall_score - flash_overall_score, 2)
            ),
            "same_decision": flash["decision"] == pro["decision"],
        }

    return {
        "schema_version": COMPARISON_RESULT_SCHEMA_VERSION,
        "event": "quality_reviewer_compare.completed",
        "passed": len(results) == 2 and not failures,
        "source_run_id": bundle.source_run_id,
        "frozen_input_sha256": input_hash,
        "deterministic_result_sha256": deterministic_result_sha256(bundle),
        "comparison_bundle_sha256": comparison_bundle_sha256(bundle),
        "same_frozen_input_for_every_call": all(
            result["input_sha256"] == input_hash for result in results
        ),
        "protocol": {
            "task_kind": REVIEW_PODCAST_DRAFT,
            "models": list(TRUSTED_REVIEWER_MODELS),
            "call_order": list(TRUSTED_REVIEWER_MODELS),
            "podcast_draft_regenerated": False,
            "deterministic_origin": bundle.deterministic_origin,
            "prompt_version": shared_prompt.version,
            "prompt_sha256": shared_prompt_hash,
            "model_review_schema_version": MODEL_REVIEW_SCHEMA_VERSION,
            "comparison_input_schema_version": COMPARISON_INPUT_SCHEMA_VERSION,
            "deterministic_facts_version": DETERMINISTIC_QUALITY_FACTS_VERSION,
            "deterministic_rules_version": DRAFT_QUALITY_RULES_VERSION,
            "chinese_style_heuristic_version": CHINESE_STYLE_HEURISTIC_VERSION,
            "scoring_formula_version": DRAFT_QUALITY_FORMULA_VERSION,
        },
        "deterministic_snapshot": {
            "score": bundle.deterministic_result.deterministic_score,
            "target_duration_minutes": (
                bundle.deterministic_result.metrics.target_duration_minutes
            ),
            "script_character_count": (bundle.deterministic_result.metrics.script_character_count),
            "estimated_duration_minutes": (
                bundle.deterministic_result.metrics.estimated_duration_minutes
            ),
            "blocker_count": sum(
                finding.status == "blocker" for finding in bundle.deterministic_result.findings
            ),
            "warning_count": sum(
                finding.status == "warning" for finding in bundle.deterministic_result.findings
            ),
        },
        "results": results,
        "comparison": comparison,
        "failures": failures,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare DeepSeek V4 Flash and Pro on one frozen podcast Draft. "
            "The command is a zero-network dry run unless --execute is present."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input",
        type=Path,
        help="strict frozen comparison input JSON",
    )
    source.add_argument(
        "--run-id",
        help="existing v6/v7 Run containing one current-contract Reviewer task",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/epiphany.db"),
        help="SQLite database used with --run-id (default: data/epiphany.db)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=(
            "sanitized comparison JSON written only during --execute "
            f"(default: {DEFAULT_OUTPUT_PATH})"
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="allow exactly two paid DeepSeek Reviewer calls",
    )
    parser.add_argument(
        "--recompute-current-rules",
        action="store_true",
        help=(
            "with --run-id, derive a new frozen deterministic snapshot from the "
            "persisted Draft using the current code; never mutates the source Run"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="explicitly allow replacing an existing local comparison JSON",
    )
    return parser


def _print_json(payload: dict[str, Any], *, stream: Any | None = None) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, indent=2),
        file=stream if stream is not None else sys.stdout,
        flush=True,
    )


async def _load_bundle(args: argparse.Namespace) -> FrozenReviewerComparisonInput:
    if args.input is not None:
        if args.recompute_current_rules:
            raise ComparisonInputInvalid("--recompute-current-rules is only valid with --run-id")
        return load_comparison_input_file(args.input)
    return await load_comparison_input_from_run(
        database_url=database_url_for_path(args.database),
        run_id=args.run_id,
        recompute_current_rules=args.recompute_current_rules,
    )


def _build_live_providers(
    *,
    settings: Settings,
    api_key: str,
) -> dict[str, ModelProvider]:
    return {
        model: DeepSeekProvider(
            api_key=api_key,
            model=model,
            billing_currency=settings.deepseek_billing_currency,
            base_url=settings.deepseek_base_url,
            quality_review_max_tokens=settings.deepseek_quality_review_max_tokens,
            max_quality_bundle_chars=settings.deepseek_max_quality_bundle_chars,
            request_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        )
        for model in TRUSTED_REVIEWER_MODELS
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    backend_dir = Path(__file__).resolve().parents[2]
    settings = Settings(_env_file=backend_dir / ".env")
    api_key = (
        settings.deepseek_api_key.get_secret_value().strip()
        if settings.deepseek_api_key is not None
        else ""
    )

    try:
        bundle = asyncio.run(_load_bundle(args))
        input_hash = frozen_input_sha256(bundle)
        deterministic_hash = deterministic_result_sha256(bundle)
        bundle_hash = comparison_bundle_sha256(bundle)
        shared_prompt_hash = prompt_sha256(
            bundle,
            max_bundle_chars=settings.deepseek_max_quality_bundle_chars,
        )
    except Exception as error:
        _print_json(
            {
                "event": "quality_reviewer_compare.blocked",
                "error_code": getattr(error, "code", "comparison_input_invalid"),
                "error_type": type(error).__name__,
                "message": "The frozen comparison input could not be validated.",
            },
            stream=sys.stderr,
        )
        return 2

    _print_json(
        build_preflight(
            execute=args.execute,
            api_key_present=bool(api_key),
            input_origin=("input_file" if args.input is not None else "persisted_run"),
            frozen_sha256=input_hash,
            deterministic_sha256=deterministic_hash,
            bundle_sha256=bundle_hash,
            prompt_hash=shared_prompt_hash,
            output_path=str(args.output),
            billing_currency=settings.deepseek_billing_currency,
            deterministic_origin=bundle.deterministic_origin,
        )
    )
    if not args.execute:
        return 0
    if args.output.exists() and not args.force:
        _print_json(
            {
                "event": "quality_reviewer_compare.blocked",
                "error_code": "comparison_output_exists",
                "message": "Choose a new --output path or add --force explicitly.",
                "output_path": str(args.output),
            },
            stream=sys.stderr,
        )
        return 2
    if not api_key:
        _print_json(
            {
                "event": "quality_reviewer_compare.blocked",
                "error_code": "deepseek_api_key_missing",
                "message": (
                    "Add EPIPHANY_DEEPSEEK_API_KEY to backend/.env, then rerun with --execute."
                ),
            },
            stream=sys.stderr,
        )
        return 2

    configure_logging(settings.log_level)
    providers = _build_live_providers(settings=settings, api_key=api_key)
    result = asyncio.run(
        compare_reviewers(
            bundle=bundle,
            providers=providers,
            max_bundle_chars=settings.deepseek_max_quality_bundle_chars,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _print_json(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
