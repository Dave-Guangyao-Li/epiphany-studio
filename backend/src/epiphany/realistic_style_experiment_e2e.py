from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from epiphany.checkpoint_e2e import (
    BACKEND_DIR,
    E2EFlowError,
    _print_json,
    _write_report,
    build_provider,
    validate_fixture_payload,
)
from epiphany.config import Settings
from epiphany.quality_contract_e2e import (
    LIVE_MODEL,
    QualityContractPaths,
    execute_e2e,
    validate_quality_contract_fixture,
)
from epiphany.runtime.providers import ModelProvider
from epiphany.schemas import CreateSourceRequest
from epiphany.writing_style_ab import (
    build_preflight as build_writing_style_ab_preflight,
)
from epiphany.writing_style_ab import (
    database_url_for_path as ab_database_url_for_path,
)
from epiphany.writing_style_ab import load_frozen_input_from_run

DEFAULT_FIXTURE_PATH = BACKEND_DIR / "fixtures/e2e/m3-7-realistic-persona/manifest.zh-CN.json"
DEFAULT_DATABASE_PATH = BACKEND_DIR / "data/m3-7-realistic-style-e2e.db"
DEFAULT_OUTPUT_DIR = BACKEND_DIR / "artifacts/m3-7-realistic-style-e2e"

EXPECTED_COUNTS = {
    "initial_task_count": 4,
    "initial_artifact_count": 6,
    "initial_model_call_count": 3,
    "final_task_count": 6,
    "final_artifact_count": 12,
    "final_model_call_count": 5,
}


def _validated_writing_samples(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or len(payload) != 4:
        raise E2EFlowError(stage="fixture", code="fixture_writing_samples_invalid")
    validated: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise E2EFlowError(
                stage="fixture",
                code="fixture_writing_samples_invalid",
            )
        sample_kind = item.get("sample_kind")
        if sample_kind not in {"written_prose", "spoken_transcript"}:
            raise E2EFlowError(
                stage="fixture",
                code="fixture_writing_sample_kind_invalid",
            )
        try:
            source = CreateSourceRequest.model_validate(item.get("source"))
        except (ValidationError, TypeError) as error:
            raise E2EFlowError(
                stage="fixture",
                code="fixture_writing_sample_source_invalid",
            ) from error
        if source.source_type != "writing_sample":
            raise E2EFlowError(
                stage="fixture",
                code="fixture_writing_sample_type_invalid",
            )
        if (
            source.metadata.get("synthetic") is not True
            or source.metadata.get("contains_personal_data") is not False
            or source.metadata.get("role") != "style_only"
        ):
            raise E2EFlowError(
                stage="fixture",
                code="fixture_writing_sample_privacy_invalid",
            )
        validated.append(
            {
                "sample_kind": sample_kind,
                "source": source.model_dump(mode="json"),
            }
        )
    titles = [item["source"]["title"] for item in validated]
    texts = [item["source"]["text"] for item in validated]
    if len(set(titles)) != len(titles) or len(set(texts)) != len(texts):
        raise E2EFlowError(
            stage="fixture",
            code="fixture_writing_samples_not_independent",
        )
    if sum(len("".join(text.split())) for text in texts) < 800:
        raise E2EFlowError(
            stage="fixture",
            code="fixture_writing_samples_too_short",
        )
    return validated


def _hydrate_source_text(source: object, *, manifest_path: Path) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise E2EFlowError(stage="fixture", code="fixture_source_invalid")
    hydrated = dict(source)
    text_file = hydrated.pop("text_file", None)
    if text_file is None:
        return hydrated
    if "text" in hydrated or not isinstance(text_file, str) or not text_file.strip():
        raise E2EFlowError(stage="fixture", code="fixture_text_file_invalid")
    fixture_root = manifest_path.parent.resolve()
    candidate = (fixture_root / text_file).resolve()
    try:
        candidate.relative_to(fixture_root)
    except ValueError as error:
        raise E2EFlowError(stage="fixture", code="fixture_text_file_outside_root") from error
    try:
        hydrated["text"] = candidate.read_text(encoding="utf-8")
    except OSError as error:
        raise E2EFlowError(stage="fixture", code="fixture_text_file_unreadable") from error
    return hydrated


def _load_hydrated_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise E2EFlowError(stage="fixture", code="fixture_unreadable") from error
    if not isinstance(payload, dict):
        raise E2EFlowError(stage="fixture", code="fixture_root_invalid")
    hydrated = dict(payload)
    initial = hydrated.get("initial_sources")
    samples = hydrated.get("writing_samples")
    if not isinstance(initial, list) or not isinstance(samples, list):
        raise E2EFlowError(stage="fixture", code="fixture_source_manifest_invalid")
    hydrated["initial_sources"] = [
        _hydrate_source_text(source, manifest_path=path) for source in initial
    ]
    hydrated["supplemental_source"] = _hydrate_source_text(
        hydrated.get("supplemental_source"),
        manifest_path=path,
    )
    hydrated["writing_samples"] = [
        {
            **item,
            "source": _hydrate_source_text(item.get("source"), manifest_path=path),
        }
        if isinstance(item, dict)
        else item
        for item in samples
    ]
    return hydrated


def load_realistic_style_fixture(path: Path) -> dict[str, Any]:
    fixture = validate_quality_contract_fixture(
        validate_fixture_payload(_load_hydrated_manifest(path))
    )
    persona = fixture.get("persona")
    if not isinstance(persona, dict) or not persona:
        raise E2EFlowError(stage="fixture", code="fixture_persona_invalid")
    writing_samples = _validated_writing_samples(fixture.get("writing_samples"))
    style_reference = fixture.get("style_reference")
    if not isinstance(style_reference, dict) or style_reference != {
        "ownership_attested": True,
        "model_processing_consent": True,
        "usage": "style_only",
    }:
        raise E2EFlowError(
            stage="fixture",
            code="fixture_style_reference_invalid",
        )
    if len(fixture["initial_sources"]) != 3:
        raise E2EFlowError(
            stage="fixture",
            code="fixture_initial_source_count_invalid",
        )
    expected = fixture["expected"]
    if expected.get("workflow_version") != "v8" or any(
        expected.get(field) != count for field, count in EXPECTED_COUNTS.items()
    ):
        raise E2EFlowError(
            stage="fixture",
            code="fixture_v8_counts_invalid",
        )
    return {
        **fixture,
        "persona": dict(persona),
        "writing_samples": writing_samples,
        "style_reference": dict(style_reference),
    }


def build_preflight(
    *,
    execute: bool,
    provider: Literal["fake", "deepseek"],
    editor_model: str,
    reviewer_model: str | None,
    api_key_present: bool,
    fixture: dict[str, Any],
    paths: QualityContractPaths,
    settings: Settings,
) -> dict[str, Any]:
    style_chars = sum(
        len("".join(item["source"]["text"].split())) for item in fixture["writing_samples"]
    )
    network_enabled = execute and provider == "deepseek" and api_key_present
    return {
        "event": "realistic_style_experiment_e2e.preflight",
        "mode": "execute" if execute else "dry-run",
        "execute_requested": execute,
        "provider": provider,
        "editor_model": editor_model if provider == "deepseek" else "fake-v1",
        "reviewer_model": (
            (reviewer_model or editor_model) if provider == "deepseek" else "fake-v1"
        ),
        "network_enabled": network_enabled,
        "paid_api_call_possible": network_enabled,
        "api_key_status": "present" if api_key_present else "absent",
        "synthetic_source_only": True,
        "fixture_id": fixture["fixture_id"],
        "persona_present": True,
        "factual_source_count": len(fixture["initial_sources"]),
        "writing_sample_source_count": len(fixture["writing_samples"]),
        "writing_sample_non_whitespace_char_count": style_chars,
        "supplemental_source_count": 1,
        "source_run_model_call_ceiling": 5,
        "subsequent_controlled_ab_call_count": 4,
        "effective_provider_limits": {
            "editor_bundle_chars": (
                settings.deepseek_max_editor_bundle_chars if provider == "deepseek" else 0
            ),
            "editor_output_tokens": (
                settings.deepseek_editor_max_tokens if provider == "deepseek" else 0
            ),
        },
        "expected": fixture["expected"],
        "safety": {
            "source_or_sample_text_in_preflight": False,
            "prompt_text_in_preflight": False,
            "api_key_in_preflight": False,
            "writing_samples_are_style_only": True,
        },
        "paths": {
            "fixture": str(paths.fixture),
            "database": str(paths.database),
            "output_dir": str(paths.output_dir),
            "runtime_log": str(paths.log),
            "report": str(paths.report),
            "interview_scaffold": str(paths.interview_scaffold),
            "podcast_draft": str(paths.podcast_draft),
            "show_notes": str(paths.show_notes),
            "quality_report_json": str(paths.quality_report_json),
            "quality_report_markdown": str(paths.quality_report_markdown),
            "safety_report": str(paths.output_dir / "safety-report.json"),
            "writing_style_ab_preflight": str(paths.output_dir / "writing-style-ab-preflight.json"),
        },
    }


def build_realistic_provider(
    *,
    provider_name: Literal["fake", "deepseek"],
    settings: Settings,
    api_key: str,
    model: Literal["deepseek-v4-flash", "deepseek-v4-pro"],
) -> ModelProvider:
    """Build the experiment Provider from the same limits shown in preflight."""

    return build_provider(
        provider_name=provider_name,
        settings=settings,
        api_key=api_key,
        model=model,
        editor_max_tokens=settings.deepseek_editor_max_tokens,
        max_editor_bundle_chars=settings.deepseek_max_editor_bundle_chars,
    )


async def execute_realistic_style_e2e(
    *,
    fixture: dict[str, Any],
    paths: QualityContractPaths,
    provider_name: Literal["fake", "deepseek"],
    editor_model: Literal["deepseek-v4-flash", "deepseek-v4-pro"],
    reviewer_model: Literal["deepseek-v4-flash", "deepseek-v4-pro"] | None,
    settings: Settings,
    api_key: str,
) -> dict[str, Any]:
    provider = build_realistic_provider(
        provider_name=provider_name,
        settings=settings,
        api_key=api_key,
        model=editor_model,
    )
    reviewer_provider = provider
    if (
        provider_name == "deepseek"
        and reviewer_model is not None
        and reviewer_model != editor_model
    ):
        reviewer_provider = build_realistic_provider(
            provider_name=provider_name,
            settings=settings,
            api_key=api_key,
            model=reviewer_model,
        )
    report = await execute_e2e(
        fixture=fixture,
        paths=paths,
        provider=provider,
        provider_name=provider_name,
        settings=settings,
        secret_values=(api_key,),
        quality_review=True,
        reviewer_provider=reviewer_provider,
    )
    run_id = str(report["final_run"]["id"])
    frozen = await load_frozen_input_from_run(
        database_url=ab_database_url_for_path(paths.database),
        run_id=run_id,
    )
    ab_preflight = build_writing_style_ab_preflight(
        frozen=frozen,
        max_editor_bundle_chars=settings.deepseek_max_editor_bundle_chars,
        max_editor_tokens=settings.deepseek_editor_max_tokens,
        max_quality_bundle_chars=settings.deepseek_max_quality_bundle_chars,
        max_quality_tokens=settings.deepseek_quality_review_max_tokens,
        api_key_present=bool(api_key),
        database_path=paths.database,
        provider_base_url=settings.deepseek_base_url,
        billing_currency=settings.deepseek_billing_currency,
    )
    ab_path = paths.output_dir / "writing-style-ab-preflight.json"
    _write_report(ab_path, ab_preflight)
    ab_ready = (
        ab_preflight["only_variable_is_writing_sample"] is True
        and ab_preflight["treatment_reaches_editor_prompt"] is True
        and ab_preflight["writing_style_readiness"] == "ready"
        and ab_preflight["writing_style_source_count"] == len(fixture["writing_samples"])
    )
    report["checks"]["controlled_ab_input_frozen"] = ab_ready
    report["failures"] = sorted(name for name, passed in report["checks"].items() if not passed)
    report["passed"] = not report["failures"]
    report["controlled_ab_preflight"] = {
        **ab_preflight,
        "path": str(ab_path),
    }
    safety_path = paths.output_dir / "safety-report.json"
    safety_report = {
        "event": "realistic_style_experiment_e2e.safety",
        "run_id": run_id,
        "passed": report["checks"]["logs_structured_and_redacted"] is True and ab_ready,
        "provider": provider_name,
        "editor_model": provider.model,
        "reviewer_model": reviewer_provider.model,
        "runtime_counts": {
            "task_count": report["final_run"]["task_count"],
            "artifact_count_before_feedback": report["final_run"]["artifact_count"],
            "artifact_count_after_feedback": report["quality"]["synthetic_feedback"][
                "final_artifact_count"
            ],
            "model_call_count": report["final_run"]["model_calls_recorded"],
        },
        "redaction": {
            "structured_runtime_log": report["logs"]["all_lines_are_json"],
            "source_style_and_generated_text_absent": report["logs"][
                "source_and_generated_text_absent"
            ],
            "contains_source_or_style_text": False,
            "contains_prompt_text": False,
            "contains_api_key": False,
        },
        "controlled_ab": {
            "only_variable_is_writing_sample": ab_preflight["only_variable_is_writing_sample"],
            "treatment_reaches_editor_prompt": ab_preflight["treatment_reaches_editor_prompt"],
            "common_experiment_contract_sha256": ab_preflight["common_experiment_contract_sha256"],
        },
    }
    _write_report(safety_path, safety_report)
    report["safety_report"] = {
        "path": str(safety_path),
        "passed": safety_report["passed"],
    }
    report["checks"]["safety_report_passed"] = safety_report["passed"]
    report["failures"] = sorted(name for name, passed in report["checks"].items() if not passed)
    report["passed"] = not report["failures"]
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise one synthetic but realistic v8 persona journey with three "
            "factual Sources, four independent style-only samples, a durable "
            "human checkpoint, Editor, Reviewer, exports, and a frozen A/B contract. "
            "Without --execute this command is read-only and makes no network calls."
        )
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--provider",
        choices=("fake", "deepseek"),
        default="fake",
    )
    parser.add_argument(
        "--editor-model",
        choices=("deepseek-v4-flash", "deepseek-v4-pro"),
        default=LIVE_MODEL,
    )
    parser.add_argument(
        "--reviewer-model",
        choices=("deepseek-v4-flash", "deepseek-v4-pro"),
        default=None,
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = QualityContractPaths(
        fixture=args.fixture.expanduser().resolve(),
        database=args.database.expanduser().resolve(),
        output_dir=args.output_dir.expanduser().resolve(),
    )
    settings = Settings(_env_file=BACKEND_DIR / ".env")
    api_key = (
        settings.deepseek_api_key.get_secret_value().strip()
        if settings.deepseek_api_key is not None
        else ""
    )
    try:
        fixture = load_realistic_style_fixture(paths.fixture)
    except E2EFlowError as error:
        _print_json(
            {
                "event": "realistic_style_experiment_e2e.blocked",
                "stage": error.stage,
                "error_code": error.code,
            },
            stream=sys.stderr,
        )
        return 2
    _print_json(
        build_preflight(
            execute=args.execute,
            provider=args.provider,
            editor_model=args.editor_model,
            reviewer_model=args.reviewer_model,
            api_key_present=bool(api_key),
            fixture=fixture,
            paths=paths,
            settings=settings,
        )
    )
    if not args.execute:
        return 0
    if args.provider == "deepseek" and not api_key:
        _print_json(
            {
                "event": "realistic_style_experiment_e2e.blocked",
                "stage": "provider",
                "error_code": "deepseek_api_key_missing",
            },
            stream=sys.stderr,
        )
        return 2
    try:
        report = asyncio.run(
            execute_realistic_style_e2e(
                fixture=fixture,
                paths=paths,
                provider_name=args.provider,
                editor_model=args.editor_model,
                reviewer_model=args.reviewer_model,
                settings=settings,
                api_key=api_key,
            )
        )
    except Exception as error:
        paths.output_dir.mkdir(parents=True, exist_ok=True)
        failure = {
            "event": "realistic_style_experiment_e2e.crashed",
            "passed": False,
            "stage": getattr(error, "stage", "unexpected"),
            "error_code": getattr(error, "code", type(error).__name__),
            "message": "Inspect only the sanitized report and runtime log.",
            "paths": {
                "fixture": str(paths.fixture),
                "database": str(paths.database),
                "output_dir": str(paths.output_dir),
                "runtime_log": str(paths.log),
                "report": str(paths.report),
            },
            "evidence": getattr(error, "safe_context", {}),
        }
        _write_report(paths.report, failure)
        _print_json(failure, stream=sys.stderr)
        return 1
    _write_report(paths.report, report)
    _print_json(
        {
            "event": "realistic_style_experiment_e2e.completed",
            "passed": report["passed"],
            "failures": report["failures"],
            "run_id": report["final_run"]["id"],
            "provider": report["runtime"]["provider"],
            "editor_model": report["runtime"]["model"],
            "reviewer_model": report["runtime"]["reviewer_model"],
            "input_tokens": report["usage"]["input_tokens"],
            "output_tokens": report["usage"]["output_tokens"],
            "estimated_costs": report["usage"]["estimated_costs"],
            "common_experiment_contract_sha256": report["controlled_ab_preflight"][
                "common_experiment_contract_sha256"
            ],
            "database_path": str(paths.database),
            "output_dir": str(paths.output_dir),
            "report_path": str(paths.report),
        }
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
