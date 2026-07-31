from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from epiphany.checkpoint_e2e import (
    BACKEND_DIR,
    E2EFlowError,
    _forbidden_log_fragments,
    _get_run,
    _poll_for_terminal,
    _request_json,
    _safe_run_summary,
    _write_report,
)
from epiphany.config import Settings
from epiphany.draft_quality_schemas import DraftQualityReport
from epiphany.editor_schemas import editor_spoken_script_reference_keys
from epiphany.guided_revision_e2e import _artifact, _markdown
from epiphany.live_deepseek_smoke import database_url_for_path
from epiphany.main import create_app
from epiphany.observability import JsonFormatter, RequestContextFilter
from epiphany.quality_contract_e2e import (
    QualityContractPaths,
    _model_calls_match_routed_providers,
    execute_e2e,
)
from epiphany.realistic_style_experiment_e2e import (
    DEFAULT_FIXTURE_PATH,
    build_realistic_provider,
    load_realistic_style_fixture,
)
from epiphany.revision_schemas import duration_character_bounds
from epiphany.runtime.providers import ModelProvider

DEFAULT_DATABASE_PATH = BACKEND_DIR / "data/m3-8-length-recovery-e2e.db"
DEFAULT_OUTPUT_DIR = BACKEND_DIR / "artifacts/m3-8-length-recovery-e2e"
REVISION_SUBMISSION_ID = "m3-8-realistic-length-recovery-v1"
REVISION_INSTRUCTION = (
    "优先展开现有未充分使用的具体事实、场景、感受和认知变化。"
    "不要重复、灌水、虚构，也不要求用完全部素材。"
)
_OPAQUE_ID_PREFIXES = (
    "art_",
    "mcall_",
    "req_",
    "run_",
    "seg_",
    "src_",
    "task_",
)


@dataclass(frozen=True)
class LengthRecoveryPaths:
    fixture: Path
    database: Path
    output_dir: Path

    @property
    def parent(self) -> QualityContractPaths:
        return QualityContractPaths(
            fixture=self.fixture,
            database=self.database,
            output_dir=self.output_dir,
        )

    @property
    def report(self) -> Path:
        return self.output_dir / "report.json"

    @property
    def revision_log(self) -> Path:
        return self.output_dir / "revision-runtime.jsonl"

    @property
    def safety_report(self) -> Path:
        return self.output_dir / "safety-report.json"

    def json_artifact(self, name: str) -> Path:
        return self.output_dir / f"{name}.json"

    def markdown(self, candidate: str, kind: str) -> Path:
        return self.output_dir / f"{candidate}-{kind}.md"


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _cost_summary(model_calls: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    micros_by_currency: dict[str, int] = {}
    for call in model_calls:
        currency = str(call.get("cost_currency") or "UNKNOWN").upper()
        micros_by_currency[currency] = micros_by_currency.get(currency, 0) + int(
            call.get("estimated_cost_micros") or 0
        )
    return {
        currency: {
            "micros": micros,
            "amount": f"{micros / 1_000_000:.6f}",
        }
        for currency, micros in sorted(micros_by_currency.items())
    }


def _script_reference_keys(artifact: dict[str, Any]) -> set[tuple[str, str]]:
    content = {key: value for key, value in artifact["content_json"].items() if key != "_execution"}
    return set(editor_spoken_script_reference_keys(content))


def _plan_body(payload: object, *, stage: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("plan"), dict):
        raise E2EFlowError(stage=stage, code="plan_invalid")
    return payload["plan"]


def _priority_reference_keys(plan_body: dict[str, Any]) -> set[tuple[str, str]]:
    material = plan_body["material"]
    references = (
        material.get("priority_candidate_source_refs", [])
        if material.get("priority_candidates_assessed") is True
        else material.get("unused_source_refs", [])
    )
    return {(str(item["source_id"]), str(item["source_segment_id"])) for item in references}


def _post_revision_plan_summary(payload: object) -> dict[str, Any]:
    plan = _plan_body(payload, stage="child_improvement_plan")
    resolution = str(plan["duration_resolution"])
    options = [
        str(option["kind"])
        for option in plan.get("options", [])
        if isinstance(option, dict) and isinstance(option.get("kind"), str)
    ]
    questions = [
        question for question in plan.get("targeted_questions", []) if isinstance(question, dict)
    ]
    return {
        "duration_resolution": resolution,
        "option_kinds": options,
        "targeted_questions": questions,
        "requires_human_action": resolution != "not_needed",
        "automatic_follow_up_revision_created": False,
    }


def _quality_report(payload: object) -> DraftQualityReport:
    if not isinstance(payload, dict) or not isinstance(payload.get("report"), dict):
        raise E2EFlowError(stage="quality", code="quality_report_response_invalid")
    return DraftQualityReport.model_validate(payload["report"])


def _duration_finding(report: DraftQualityReport) -> str:
    finding = next(
        (
            item
            for item in report.deterministic.findings
            if item.code == "draft.empty" or item.code.startswith("duration.")
        ),
        None,
    )
    if finding is None:
        raise E2EFlowError(stage="quality", code="duration_finding_missing")
    return finding.status


def _finding_status(report: DraftQualityReport, code: str) -> str | None:
    finding = next(
        (item for item in report.deterministic.findings if item.code == code),
        None,
    )
    return None if finding is None else finding.status


def _warning_count(report: DraftQualityReport) -> int:
    return sum(finding.status == "warning" for finding in report.deterministic.findings)


def _non_duration_warning_count(report: DraftQualityReport) -> int:
    """Count quality warnings without penalizing an improved duration severity.

    A Draft can legitimately move from a duration blocker to a duration warning.
    Comparing raw warning counts would then treat that improvement as a new
    warning, even when every non-duration quality signal stayed flat or improved.
    Duration has its own explicit recovery checks, so this comparison only covers
    the remaining deterministic findings.
    """

    return sum(
        finding.status == "warning"
        for finding in report.deterministic.findings
        if finding.code != "draft.empty" and not finding.code.startswith("duration.")
    )


def _density_per_1000(*, count: int, character_count: int) -> float:
    return round(count / max(1, character_count) * 1_000, 4)


def _chinese_style_density(report: DraftQualityReport) -> float:
    metrics = report.deterministic.metrics
    pattern_counts = metrics.chinese_style_pattern_counts.model_dump(mode="json")
    return _density_per_1000(
        count=sum(int(value) for value in pattern_counts.values()),
        character_count=metrics.script_character_count,
    )


def _model_dimension_scores(
    report: DraftQualityReport,
) -> dict[str, int | None]:
    if report.model_self_review is None:
        return {}
    return {
        dimension.dimension: (dimension.score if dimension.assessable else None)
        for dimension in report.model_self_review.dimensions
    }


def _long_string_fragments(value: object, *, minimum_length: int = 32) -> list[str]:
    fragments: list[str] = []
    if isinstance(value, str):
        normalized = value.strip()
        # Opaque identifiers are safe trace metadata, not generated prose or
        # private source material. Treating them as secrets would make every
        # valid structured Run log fail the content-leak check.
        if len(normalized) >= minimum_length and not normalized.startswith(_OPAQUE_ID_PREFIXES):
            fragments.append(normalized)
    elif isinstance(value, dict):
        for nested in value.values():
            fragments.extend(
                _long_string_fragments(
                    nested,
                    minimum_length=minimum_length,
                )
            )
    elif isinstance(value, list):
        for nested in value:
            fragments.extend(
                _long_string_fragments(
                    nested,
                    minimum_length=minimum_length,
                )
            )
    return fragments


def _sensitive_log_fragments(value: object) -> list[str]:
    """Return full generated strings plus partial windows that must stay out of logs."""

    fragments: set[str] = set()
    for text in _long_string_fragments(value):
        fragments.add(text)
        for start in range(0, len(text), 32):
            chunk = text[start : start + 48]
            if len(chunk) >= 16:
                fragments.add(chunk)
    return sorted(fragments, key=lambda item: (-len(item), item))


def _log_summary(
    paths: Sequence[Path],
    *,
    forbidden_fragments: Sequence[str],
    required_events: set[str] | None = None,
) -> tuple[dict[str, Any], bool]:
    rows: list[dict[str, Any]] = []
    raw_parts: list[str] = []
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        raw_parts.append(raw)
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise E2EFlowError(stage="log_validation", code="log_json_invalid") from error
            if not isinstance(row, dict):
                raise E2EFlowError(stage="log_validation", code="log_row_invalid")
            rows.append(row)
    combined = "\n".join(raw_parts)
    counts = Counter(str(row["event"]) for row in rows if row.get("event"))
    required = required_events or {
        "run.waiting_for_user",
        "run.resume.accepted",
        "workflow.draft_improvement.planned",
        "workflow.draft_revision.requested",
        "workflow.draft_revision.queued",
        "workflow.draft_revision.compared",
    }
    redacted = all(
        fragment not in combined
        for fragment in forbidden_fragments
        if fragment and len(fragment) >= 8
    )
    valid = bool(rows) and required.issubset(counts) and redacted
    return (
        {
            "paths": [str(path) for path in paths],
            "line_count": len(rows),
            "event_counts": dict(sorted(counts.items())),
            "required_events": sorted(required),
            "required_events_present": required.issubset(counts),
            "source_sample_prompt_and_key_absent": redacted,
        },
        valid,
    )


def build_preflight(
    *,
    execute: bool,
    provider: Literal["fake", "deepseek"],
    editor_model: str,
    reviewer_model: str,
    api_key_present: bool,
    fixture: dict[str, Any],
    paths: LengthRecoveryPaths,
) -> dict[str, Any]:
    network_enabled = execute and provider == "deepseek" and api_key_present
    brief = fixture["creative_brief"]
    target = int(brief["target_duration_minutes"]) * int(brief["speaking_rate_chars_per_minute"])
    return {
        "event": "length_recovery_e2e.preflight",
        "mode": "execute" if execute else "dry-run",
        "execute_requested": execute,
        "provider": provider,
        "editor_model": editor_model if provider == "deepseek" else "fake-v1",
        "reviewer_model": reviewer_model if provider == "deepseek" else "fake-v1",
        "network_enabled": network_enabled,
        "paid_api_call_possible": network_enabled,
        "api_key_status": "present" if api_key_present else "absent",
        "synthetic_source_only": True,
        "fixture_id": fixture["fixture_id"],
        "target_duration_minutes": brief["target_duration_minutes"],
        "duration_character_range": {
            "minimum": (target * 85 + 99) // 100,
            "target": target,
            "maximum": target * 115 // 100,
        },
        "model_call_ceiling": {
            "parent": 5,
            "child_revision": 2,
            "total": 7,
            "hidden_retry": False,
        },
        "revision": {
            "selected_actions": ["reuse_unused_material"],
            "new_source_count": 0,
            "lower_target_duration": False,
            "automatic_loop": False,
        },
        "paths": {
            "fixture": str(paths.fixture),
            "database": str(paths.database),
            "output_dir": str(paths.output_dir),
            "parent_runtime_log": str(paths.parent.log),
            "revision_runtime_log": str(paths.revision_log),
            "report": str(paths.report),
        },
        "safety": {
            "source_sample_prompt_or_key_in_preflight": False,
            "human_action_is_simulated_explicitly": True,
        },
    }


def _revision_settings(
    *,
    database_url: str,
    provider_name: Literal["fake", "deepseek"],
    settings: Settings,
) -> Settings:
    return Settings(
        database_url=database_url,
        create_schema_on_start=False,
        log_level="INFO",
        worker_enabled=True,
        worker_poll_interval_seconds=0.02,
        worker_max_concurrency=1,
        worker_lease_seconds=150,
        task_timeout_seconds=120,
        task_max_attempts=1,
        model_max_calls_per_run=2,
        model_provider=provider_name,
        deepseek_billing_currency=settings.deepseek_billing_currency,
    )


def _terminal_error_code(run: dict[str, Any]) -> str:
    for task in reversed(list(run.get("tasks", []))):
        error_code = task.get("error_code")
        if task.get("status") == "failed" and isinstance(error_code, str) and error_code:
            return error_code
    return f"child_run_{run.get('status') or 'terminal'}"


def _usage_summary(
    *,
    parent_calls: list[dict[str, Any]],
    child_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    all_calls = [*parent_calls, *child_calls]

    def summarize(calls: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "model_call_count": len(calls),
            "input_tokens": sum(int(call.get("input_tokens") or 0) for call in calls),
            "output_tokens": sum(int(call.get("output_tokens") or 0) for call in calls),
            "duration_ms": sum(int(call.get("duration_ms") or 0) for call in calls),
            "estimated_costs": _cost_summary(calls),
        }

    return {
        "parent": summarize(parent_calls),
        "child": summarize(child_calls),
        "total": summarize(all_calls),
    }


def _failed_child_revision_result(
    *,
    paths: LengthRecoveryPaths,
    fixture: dict[str, Any],
    api_key: str,
    parent_before: dict[str, Any],
    parent_after: dict[str, Any],
    parent_snapshot: dict[str, Any],
    parent_draft: dict[str, Any],
    parent_quality_payload: dict[str, Any],
    plan: dict[str, Any],
    revision_body: dict[str, Any],
    created: dict[str, Any],
    replay: dict[str, Any],
    child: dict[str, Any],
    markdown: dict[str, str],
    provider: ModelProvider,
    reviewer_provider: ModelProvider,
) -> dict[str, Any]:
    """Close a failed child Run without looking for artifacts that cannot exist."""

    parent_quality = _quality_report(parent_quality_payload)
    parent_metrics = parent_quality.deterministic.metrics
    parent_refs = _script_reference_keys(parent_draft)
    priority_refs = _priority_reference_keys(_plan_body(plan, stage="improvement_plan"))
    minimum, maximum = duration_character_bounds(
        int(plan["plan"]["duration"]["target_script_character_count"])
    )
    persisted_parent_draft = _artifact(
        parent_after,
        artifact_id=str(parent_snapshot["output_artifact_id"]),
    )
    persisted_parent_quality = _artifact(
        parent_after,
        kind="draft_quality_report",
    )
    terminal_error_code = _terminal_error_code(child)
    parent_calls = list(parent_before["model_calls"])
    child_calls = list(child["model_calls"])
    child_tasks = list(child["tasks"])

    for name, content in markdown.items():
        candidate, kind = name.split("-", maxsplit=1)
        paths.markdown(candidate, kind).write_text(content, encoding="utf-8")
    paths.json_artifact("improvement-plan").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths.json_artifact("revision-request").write_text(
        json.dumps(
            {
                **revision_body,
                "request_artifact_id": created["request_artifact_id"],
                "child_run_id": child["id"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths.json_artifact("parent-quality-report").write_text(
        json.dumps(parent_quality_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    forbidden = [
        *_forbidden_log_fragments(fixture, secret_values=(api_key,)),
        *(item["source"]["text"] for item in fixture["writing_samples"]),
        REVISION_INSTRUCTION,
        *_sensitive_log_fragments(parent_draft["content_json"]),
        *_sensitive_log_fragments(parent_quality_payload),
        *(
            fragment
            for content in markdown.values()
            for fragment in _sensitive_log_fragments(content.splitlines())
        ),
    ]
    logs, logs_valid = _log_summary(
        [paths.parent.log, paths.revision_log],
        forbidden_fragments=forbidden,
        required_events={
            "run.waiting_for_user",
            "run.resume.accepted",
            "workflow.draft_improvement.planned",
            "workflow.draft_revision.requested",
            "workflow.draft_revision.queued",
            "worker.task.failed",
        },
    )
    workflow_checks = {
        "parent_workflow_succeeded": parent_before["status"] == "succeeded",
        "plan_reuses_existing_material": (
            plan["plan"]["duration_resolution"]
            in {"reuse_unused_material", "reuse_then_supplement"}
            and bool(priority_refs)
        ),
        "revision_request_is_explicit_and_idempotent": (
            created["idempotent_replay"] is False
            and replay["idempotent_replay"] is True
            and created["run"]["id"] == replay["run"]["id"]
        ),
        "child_terminal_failure_preserved": (
            child.get("status") in {"failed", "cancelled"} and bool(terminal_error_code)
        ),
        "child_execution_is_one_call_without_retry": (
            len(child_tasks) == 1
            and child_tasks[0].get("kind") == "revise_podcast_draft"
            and child_tasks[0].get("attempt") == 1
            and len(child_calls) == 1
            and child_calls[0].get("attempt") == 1
        ),
        "child_call_matches_editor_provider": _model_calls_match_routed_providers(
            model_calls=child_calls,
            tasks=child_tasks,
            primary_provider=provider,
            reviewer_provider=reviewer_provider,
            expected_model_calls=1,
        ),
        "reviewer_and_comparison_not_requested": (
            all(task.get("kind") != "review_podcast_draft" for task in child_tasks)
        ),
        "parent_is_immutable": (
            _canonical_sha256(persisted_parent_draft["content_json"])
            == parent_snapshot["draft_sha256"]
            and _canonical_sha256(persisted_parent_quality["content_json"])
            == parent_snapshot["quality_sha256"]
            and parent_after["output_artifact_id"] == parent_snapshot["output_artifact_id"]
            and len(parent_after["model_calls"]) == parent_snapshot["model_calls"]
            and len(parent_after["tasks"]) == parent_snapshot["tasks"]
        ),
        "logs_structured_and_redacted": logs_valid,
        # This is intentionally false: a terminal child failure is a completed,
        # analyzable experiment outcome, not a successful Revision.
        "child_revision_succeeded": False,
    }
    content_checks = {
        # No child Draft was committed, so content acceptance must not be
        # inferred from the parent or from a provider response rejected locally.
        "child_content_acceptance_available": False,
    }
    parent_template_density = _density_per_1000(
        count=parent_metrics.template_phrase_count,
        character_count=parent_metrics.script_character_count,
    )
    parent_not_but_density = _density_per_1000(
        count=parent_metrics.not_but_pattern_count,
        character_count=parent_metrics.script_character_count,
    )
    workflow_failures = sorted(name for name, passed in workflow_checks.items() if not passed)
    content_failures = sorted(name for name, passed in content_checks.items() if not passed)
    return {
        "parent": _safe_run_summary(parent_after),
        "child": {
            **_safe_run_summary(child),
            "terminal_error_code": terminal_error_code,
        },
        "workflow": {
            "plan_artifact_id": plan["artifact"]["id"],
            "revision_request_artifact_id": created["request_artifact_id"],
            "comparison_artifact_id": None,
            "child_improvement_plan_requested": False,
            "reviewer_requested": False,
            "comparison_requested": False,
            "automatic_revision_count": 0,
            "explicit_revision_count": 1,
        },
        "post_revision_next_action": None,
        "child_plan": None,
        "material_utilization": {
            "priority_unused_ref_count": len(priority_refs),
            "parent_spoken_ref_count": len(parent_refs),
            "child_spoken_ref_count": None,
            "newly_used_priority_ref_count": None,
            "still_unused_priority_ref_count": len(priority_refs),
            "all_material_used_required": False,
            "status": "not_evaluated_child_failed",
        },
        "quality": {
            "minimum_script_character_count": minimum,
            "target_script_character_count": plan["plan"]["duration"][
                "target_script_character_count"
            ],
            "maximum_script_character_count": maximum,
            "parent": {
                "script_character_count": parent_metrics.script_character_count,
                "estimated_duration_minutes": parent_metrics.estimated_duration_minutes,
                "decision": parent_quality.decision,
                "duration_status": _duration_finding(parent_quality),
                "deterministic_score": parent_quality.deterministic.deterministic_score,
                "warning_count": _warning_count(parent_quality),
                "non_duration_warning_count": _non_duration_warning_count(parent_quality),
                "template_phrase_density_per_1000_chars": parent_template_density,
                "not_but_density_per_1000_chars": parent_not_but_density,
                "chinese_style_density_per_1000_chars": _chinese_style_density(parent_quality),
                "model_dimension_scores": _model_dimension_scores(parent_quality),
            },
            "child": {
                "status": "not_evaluated",
                "reason": "child_run_failed_before_draft_commit",
                "terminal_error_code": terminal_error_code,
            },
            "script_character_delta": None,
        },
        "usage": _usage_summary(
            parent_calls=parent_calls,
            child_calls=child_calls,
        ),
        "logs": logs,
        "workflow_checks": workflow_checks,
        "content_checks": content_checks,
        "workflow_failures": workflow_failures,
        "content_failures": content_failures,
        "outputs": {
            "improvement_plan": str(paths.json_artifact("improvement-plan")),
            "child_improvement_plan": None,
            "revision_request": str(paths.json_artifact("revision-request")),
            "comparison": None,
            "parent_quality_json": str(paths.json_artifact("parent-quality-report")),
            "child_quality_json": None,
            "markdown": {
                name: str(paths.markdown(*name.split("-", maxsplit=1))) for name in markdown
            },
            "unavailable": {
                "child_podcast_draft": "child_run_failed_before_draft_commit",
                "child_show_notes": "child_run_failed_before_draft_commit",
                "child_quality_report": "reviewer_not_requested",
                "revision_comparison": "reviewer_not_requested",
                "child_improvement_plan": "child_run_failed_before_draft_commit",
            },
        },
    }


async def _continue_with_revision(
    *,
    parent_run_id: str,
    paths: LengthRecoveryPaths,
    fixture: dict[str, Any],
    provider_name: Literal["fake", "deepseek"],
    provider: ModelProvider,
    reviewer_provider: ModelProvider,
    settings: Settings,
    api_key: str,
) -> dict[str, Any]:
    handler = logging.FileHandler(paths.revision_log, mode="w", encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())
    logger = logging.getLogger("epiphany")
    old_handlers = set(logger.handlers)
    old_levels = {item: item.level for item in old_handlers}
    old_level = logger.level
    old_propagate = logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        app = create_app(
            settings=_revision_settings(
                database_url=database_url_for_path(paths.database),
                provider_name=provider_name,
                settings=settings,
            ),
            provider=provider,
            reviewer_provider=reviewer_provider,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://epiphany-length-recovery-e2e",
                timeout=30,
            ) as client:
                parent_before = await _get_run(
                    client,
                    parent_run_id,
                    stage="parent_before_revision",
                    request_id="req_m38_parent_before",
                )
                parent_draft = _artifact(
                    parent_before,
                    artifact_id=str(parent_before["output_artifact_id"]),
                )
                parent_quality_artifact = _artifact(
                    parent_before,
                    kind="draft_quality_report",
                )
                parent_snapshot = {
                    "draft_sha256": _canonical_sha256(parent_draft["content_json"]),
                    "quality_sha256": _canonical_sha256(parent_quality_artifact["content_json"]),
                    "output_artifact_id": parent_before["output_artifact_id"],
                    "model_calls": len(parent_before["model_calls"]),
                    "tasks": len(parent_before["tasks"]),
                }
                parent_quality_payload = await _request_json(
                    client,
                    "GET",
                    f"/runs/{parent_run_id}/quality-report",
                    stage="parent_quality",
                    request_id="req_m38_parent_quality",
                    expected_statuses={200},
                )
                plan = await _request_json(
                    client,
                    "GET",
                    f"/runs/{parent_run_id}/improvement-plan",
                    stage="improvement_plan",
                    request_id="req_m38_plan",
                    expected_statuses={200},
                )
                if not isinstance(plan, dict):
                    raise E2EFlowError(stage="improvement_plan", code="response_invalid")
                plan_body = _plan_body(plan, stage="improvement_plan")
                reuse_option_available = any(
                    isinstance(option, dict) and option.get("kind") == "reuse_unused_material"
                    for option in plan_body.get("options", [])
                )
                if (
                    plan_body.get("duration_resolution")
                    not in {"reuse_unused_material", "reuse_then_supplement"}
                    or not reuse_option_available
                ):
                    raise E2EFlowError(
                        stage="improvement_plan",
                        code="reuse_unused_material_not_available",
                        safe_context={
                            "duration_resolution": plan_body.get("duration_resolution"),
                            "reuse_option_available": reuse_option_available,
                        },
                    )
                revision_body = {
                    "submission_id": REVISION_SUBMISSION_ID,
                    "selected_actions": ["reuse_unused_material"],
                    "selected_feedback_artifact_ids": [],
                    "selected_gap_codes": [],
                    "source_ids": [],
                    "target_duration_minutes": None,
                    "revision_instruction": REVISION_INSTRUCTION,
                }
                created = await _request_json(
                    client,
                    "POST",
                    f"/runs/{parent_run_id}/revisions",
                    stage="create_revision",
                    request_id="req_m38_revision",
                    expected_statuses={201},
                    json_body=revision_body,
                )
                replay = await _request_json(
                    client,
                    "POST",
                    f"/runs/{parent_run_id}/revisions",
                    stage="revision_replay",
                    request_id="req_m38_revision_replay",
                    expected_statuses={200},
                    json_body=revision_body,
                )
                if not isinstance(created, dict) or not isinstance(replay, dict):
                    raise E2EFlowError(stage="create_revision", code="response_invalid")
                child_run_id = str(created["run"]["id"])
                child = await _poll_for_terminal(client, child_run_id)
                child_succeeded = child.get("status") == "succeeded"
                child_draft = (
                    _artifact(
                        child,
                        artifact_id=str(child["output_artifact_id"]),
                    )
                    if child_succeeded
                    else None
                )
                child_quality_payload = (
                    await _request_json(
                        client,
                        "GET",
                        f"/runs/{child_run_id}/quality-report",
                        stage="child_quality",
                        request_id="req_m38_child_quality",
                        expected_statuses={200},
                    )
                    if child_succeeded
                    else None
                )
                comparison = (
                    await _request_json(
                        client,
                        "GET",
                        f"/runs/{child_run_id}/revision-comparison",
                        stage="comparison",
                        request_id="req_m38_comparison",
                        expected_statuses={200},
                    )
                    if child_succeeded
                    else None
                )
                child_plan = (
                    await _request_json(
                        client,
                        "GET",
                        f"/runs/{child_run_id}/improvement-plan",
                        stage="child_improvement_plan",
                        request_id="req_m38_child_plan",
                        expected_statuses={200},
                    )
                    if child_succeeded
                    else None
                )
                child_after_plan = (
                    await _get_run(
                        client,
                        child_run_id,
                        stage="child_after_improvement_plan",
                        request_id="req_m38_child_after_plan",
                    )
                    if child_succeeded
                    else child
                )
                parent_after = await _get_run(
                    client,
                    parent_run_id,
                    stage="parent_after_revision",
                    request_id="req_m38_parent_after",
                )
                markdown = {
                    "parent-podcast-draft": await _markdown(
                        client,
                        run_id=parent_run_id,
                        kind="podcast-draft",
                        request_id="req_m38_parent_draft",
                    ),
                    "parent-show-notes": await _markdown(
                        client,
                        run_id=parent_run_id,
                        kind="show-notes",
                        request_id="req_m38_parent_notes",
                    ),
                    "parent-quality-report": await _markdown(
                        client,
                        run_id=parent_run_id,
                        kind="quality-report",
                        request_id="req_m38_parent_quality_md",
                    ),
                }
                if child_succeeded:
                    markdown.update(
                        {
                            "child-podcast-draft": await _markdown(
                                client,
                                run_id=child_run_id,
                                kind="podcast-draft",
                                request_id="req_m38_child_draft",
                            ),
                            "child-show-notes": await _markdown(
                                client,
                                run_id=child_run_id,
                                kind="show-notes",
                                request_id="req_m38_child_notes",
                            ),
                            "child-quality-report": await _markdown(
                                client,
                                run_id=child_run_id,
                                kind="quality-report",
                                request_id="req_m38_child_quality_md",
                            ),
                        }
                    )
    finally:
        logger.removeHandler(handler)
        handler.close()
        for extra in list(logger.handlers):
            if extra not in old_handlers and getattr(extra, "_epiphany_json_handler", False):
                logger.removeHandler(extra)
                extra.close()
        for item, level in old_levels.items():
            item.setLevel(level)
        logger.setLevel(old_level)
        logger.propagate = old_propagate

    if not child_succeeded:
        return _failed_child_revision_result(
            paths=paths,
            fixture=fixture,
            api_key=api_key,
            parent_before=parent_before,
            parent_after=parent_after,
            parent_snapshot=parent_snapshot,
            parent_draft=parent_draft,
            parent_quality_payload=parent_quality_payload,
            plan=plan,
            revision_body=revision_body,
            created=created,
            replay=replay,
            child=child,
            markdown=markdown,
            provider=provider,
            reviewer_provider=reviewer_provider,
        )

    parent_quality = _quality_report(parent_quality_payload)
    child_quality = _quality_report(child_quality_payload)
    parent_metrics = parent_quality.deterministic.metrics
    child_metrics = child_quality.deterministic.metrics
    parent_model_scores = _model_dimension_scores(parent_quality)
    child_model_scores = _model_dimension_scores(child_quality)
    critical_reviewer_dimensions = {
        "source_faithfulness",
        "coverage_and_specificity",
        "conciseness_and_non_redundancy",
    }
    parent_refs = _script_reference_keys(parent_draft)
    child_refs = _script_reference_keys(child_draft)
    priority_refs = _priority_reference_keys(_plan_body(plan, stage="improvement_plan"))
    child_plan_body = _plan_body(child_plan, stage="child_improvement_plan")
    post_revision = _post_revision_plan_summary(child_plan)
    writing_sample_ids = {
        str(sample["source_id"])
        for sample in parent_before["input_json"]
        .get("writing_style_reference", {})
        .get("samples", [])
    }
    # The Improvement Plan inventory is built from the persisted initial and
    # supplemental factual segments. Spoken parent refs plus unused plan refs
    # therefore reconstruct the full factual Source set without exposing Task
    # input payloads through the public Run API.
    factual_ids = {source_id for source_id, _segment_id in (parent_refs | priority_refs)}
    newly_used_refs = child_refs - parent_refs
    still_unused_refs = priority_refs - child_refs

    persisted_parent_draft = _artifact(
        parent_after,
        artifact_id=str(parent_snapshot["output_artifact_id"]),
    )
    persisted_parent_quality = _artifact(
        parent_after,
        kind="draft_quality_report",
    )
    minimum, maximum = duration_character_bounds(
        int(plan["plan"]["duration"]["target_script_character_count"])
    )
    workflow_checks = {
        "parent_workflow_succeeded": parent_before["status"] == "succeeded",
        "plan_reuses_existing_material": (
            plan["plan"]["duration_resolution"]
            in {"reuse_unused_material", "reuse_then_supplement"}
            and bool(priority_refs)
        ),
        "revision_request_is_explicit_and_idempotent": (
            created["idempotent_replay"] is False
            and replay["idempotent_replay"] is True
            and created["run"]["id"] == replay["run"]["id"]
        ),
        "child_is_one_two_call_revision": (
            child["status"] == "succeeded"
            and child["parent_run_id"] == parent_run_id
            and len(child["tasks"]) == 2
            and len(child["model_calls"]) == 2
            and {task["kind"] for task in child["tasks"]}
            == {"revise_podcast_draft", "review_podcast_draft"}
        ),
        "child_calls_match_routed_providers": (
            _model_calls_match_routed_providers(
                model_calls=list(child["model_calls"]),
                tasks=list(child["tasks"]),
                primary_provider=provider,
                reviewer_provider=reviewer_provider,
                expected_model_calls=2,
            )
        ),
        "parent_is_immutable": (
            _canonical_sha256(persisted_parent_draft["content_json"])
            == parent_snapshot["draft_sha256"]
            and _canonical_sha256(persisted_parent_quality["content_json"])
            == parent_snapshot["quality_sha256"]
            and parent_after["output_artifact_id"] == parent_snapshot["output_artifact_id"]
            and len(parent_after["model_calls"]) == parent_snapshot["model_calls"]
            and len(parent_after["tasks"]) == parent_snapshot["tasks"]
        ),
        "comparison_keeps_human_choice": (
            isinstance(comparison, dict)
            and comparison["comparison"]["automatic_winner_selected"] is False
            and comparison["comparison"]["requires_human_review"] is True
        ),
        "post_revision_plan_matches_child_duration": (
            (
                child_metrics.script_character_count >= minimum
                and child_plan_body["duration_resolution"] == "not_needed"
            )
            or (
                child_metrics.script_character_count < minimum
                and child_plan_body["duration_resolution"] != "not_needed"
            )
        ),
        "post_revision_plan_does_not_queue_automatic_work": (
            len(child_after_plan["tasks"]) == len(child["tasks"])
            and len(child_after_plan["model_calls"]) == len(child["model_calls"])
        ),
    }
    parent_template_density = _density_per_1000(
        count=parent_metrics.template_phrase_count,
        character_count=parent_metrics.script_character_count,
    )
    child_template_density = _density_per_1000(
        count=child_metrics.template_phrase_count,
        character_count=child_metrics.script_character_count,
    )
    parent_not_but_density = _density_per_1000(
        count=parent_metrics.not_but_pattern_count,
        character_count=parent_metrics.script_character_count,
    )
    child_not_but_density = _density_per_1000(
        count=child_metrics.not_but_pattern_count,
        character_count=child_metrics.script_character_count,
    )
    parent_chinese_style_density = _chinese_style_density(parent_quality)
    child_chinese_style_density = _chinese_style_density(child_quality)
    content_checks = {
        "parent_is_below_minimum": parent_metrics.script_character_count < minimum,
        "child_reaches_duration_range": (
            minimum <= child_metrics.script_character_count <= maximum
        ),
        "child_has_no_deterministic_blocker": (not child_quality.deterministic.has_blocker),
        "deterministic_score_not_lower": (
            child_quality.deterministic.deterministic_score
            >= parent_quality.deterministic.deterministic_score
        ),
        "non_duration_warning_count_not_higher": (
            _non_duration_warning_count(child_quality)
            <= _non_duration_warning_count(parent_quality)
        ),
        "duration_blocker_removed": _duration_finding(child_quality) != "blocker",
        "new_priority_evidence_used": bool(newly_used_refs & priority_refs),
        "all_child_refs_are_factual": all(
            source_id in factual_ids for source_id, _segment_id in child_refs
        ),
        "writing_samples_never_cited_as_facts": all(
            source_id not in writing_sample_ids for source_id, _segment_id in child_refs
        ),
        "paragraph_citations_complete": child_metrics.paragraph_citation_coverage == 1.0,
        "no_exact_duplicate_paragraphs": child_metrics.exact_duplicate_paragraph_count == 0,
        "repeated_windows_not_warning": _finding_status(
            child_quality,
            "repetition.eight_character_windows",
        )
        != "warning",
        "filler_not_warning": _finding_status(child_quality, "style.filler_phrases") != "warning",
        "avoid_pattern_hits_not_higher": (
            child_metrics.avoid_pattern_hit_count <= parent_metrics.avoid_pattern_hit_count
        ),
        "template_density_not_materially_higher": (
            child_template_density <= parent_template_density + 1.0
        ),
        "not_but_density_not_materially_higher": (
            child_not_but_density <= parent_not_but_density + 1.0
        ),
        "chinese_style_density_not_materially_higher": (
            child_chinese_style_density <= parent_chinese_style_density + 1.0
        ),
        "critical_reviewer_dimensions_supported": (
            bool(child_model_scores)
            and all(
                child_model_scores.get(dimension) is not None
                and int(child_model_scores[dimension] or 0) >= 3
                for dimension in critical_reviewer_dimensions
            )
        ),
    }
    for name, content in markdown.items():
        candidate, kind = name.split("-", maxsplit=1)
        paths.markdown(candidate, kind).write_text(content, encoding="utf-8")
    paths.json_artifact("improvement-plan").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths.json_artifact("revision-request").write_text(
        json.dumps(
            {
                **revision_body,
                "request_artifact_id": created["request_artifact_id"],
                "child_run_id": child["id"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths.json_artifact("parent-quality-report").write_text(
        json.dumps(parent_quality_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths.json_artifact("child-quality-report").write_text(
        json.dumps(child_quality_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths.json_artifact("revision-comparison").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths.json_artifact("post-revision-improvement-plan").write_text(
        json.dumps(child_plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    forbidden = [
        *_forbidden_log_fragments(fixture, secret_values=(api_key,)),
        *(item["source"]["text"] for item in fixture["writing_samples"]),
        REVISION_INSTRUCTION,
        *_sensitive_log_fragments(parent_draft["content_json"]),
        *_sensitive_log_fragments(child_draft["content_json"]),
        *_sensitive_log_fragments(parent_quality_payload),
        *_sensitive_log_fragments(child_quality_payload),
        *_sensitive_log_fragments(child_plan),
        *(
            fragment
            for content in markdown.values()
            for fragment in _sensitive_log_fragments(content.splitlines())
        ),
    ]
    logs, logs_valid = _log_summary(
        [paths.parent.log, paths.revision_log],
        forbidden_fragments=forbidden,
    )
    workflow_checks["logs_structured_and_redacted"] = logs_valid

    parent_calls = list(parent_before["model_calls"])
    child_calls = list(child["model_calls"])
    workflow_failures = sorted(name for name, passed in workflow_checks.items() if not passed)
    content_failures = sorted(name for name, passed in content_checks.items() if not passed)
    return {
        "parent": _safe_run_summary(parent_after),
        "child": _safe_run_summary(child_after_plan),
        "workflow": {
            "plan_artifact_id": plan["artifact"]["id"],
            "revision_request_artifact_id": created["request_artifact_id"],
            "comparison_artifact_id": comparison["artifact"]["id"],
            "child_improvement_plan_artifact_id": child_plan["artifact"]["id"],
            "automatic_revision_count": 0,
            "explicit_revision_count": 1,
        },
        "post_revision_next_action": post_revision["duration_resolution"],
        "child_plan": {
            **child_plan,
            "next_action": post_revision,
        },
        "material_utilization": {
            "priority_unused_ref_count": len(priority_refs),
            "parent_spoken_ref_count": len(parent_refs),
            "child_spoken_ref_count": len(child_refs),
            "newly_used_priority_ref_count": len(newly_used_refs & priority_refs),
            "still_unused_priority_ref_count": len(still_unused_refs),
            "all_material_used_required": False,
        },
        "quality": {
            "minimum_script_character_count": minimum,
            "target_script_character_count": plan["plan"]["duration"][
                "target_script_character_count"
            ],
            "maximum_script_character_count": maximum,
            "parent": {
                "script_character_count": parent_metrics.script_character_count,
                "estimated_duration_minutes": parent_metrics.estimated_duration_minutes,
                "decision": parent_quality.decision,
                "duration_status": _duration_finding(parent_quality),
                "deterministic_score": parent_quality.deterministic.deterministic_score,
                "warning_count": _warning_count(parent_quality),
                "non_duration_warning_count": _non_duration_warning_count(parent_quality),
                "template_phrase_density_per_1000_chars": parent_template_density,
                "not_but_density_per_1000_chars": parent_not_but_density,
                "chinese_style_density_per_1000_chars": (parent_chinese_style_density),
                "model_dimension_scores": parent_model_scores,
            },
            "child": {
                "script_character_count": child_metrics.script_character_count,
                "estimated_duration_minutes": child_metrics.estimated_duration_minutes,
                "decision": child_quality.decision,
                "duration_status": _duration_finding(child_quality),
                "deterministic_score": child_quality.deterministic.deterministic_score,
                "exact_duplicate_paragraph_count": (child_metrics.exact_duplicate_paragraph_count),
                "repeated_eight_character_window_ratio": (
                    child_metrics.repeated_eight_character_window_ratio
                ),
                "filler_phrase_density_per_1000_chars": (
                    child_metrics.filler_phrase_density_per_1000_chars
                ),
                "warning_count": _warning_count(child_quality),
                "non_duration_warning_count": _non_duration_warning_count(child_quality),
                "template_phrase_density_per_1000_chars": child_template_density,
                "not_but_density_per_1000_chars": child_not_but_density,
                "chinese_style_density_per_1000_chars": (child_chinese_style_density),
                "model_dimension_scores": child_model_scores,
            },
            "script_character_delta": (
                child_metrics.script_character_count - parent_metrics.script_character_count
            ),
        },
        "usage": _usage_summary(
            parent_calls=parent_calls,
            child_calls=child_calls,
        ),
        "logs": logs,
        "workflow_checks": workflow_checks,
        "content_checks": content_checks,
        "workflow_failures": workflow_failures,
        "content_failures": content_failures,
        "outputs": {
            "improvement_plan": str(paths.json_artifact("improvement-plan")),
            "child_improvement_plan": str(paths.json_artifact("post-revision-improvement-plan")),
            "revision_request": str(paths.json_artifact("revision-request")),
            "comparison": str(paths.json_artifact("revision-comparison")),
            "parent_quality_json": str(paths.json_artifact("parent-quality-report")),
            "child_quality_json": str(paths.json_artifact("child-quality-report")),
            "markdown": {
                name: str(paths.markdown(*name.split("-", maxsplit=1))) for name in markdown
            },
        },
    }


async def execute_length_recovery_e2e(
    *,
    fixture: dict[str, Any],
    paths: LengthRecoveryPaths,
    provider_name: Literal["fake", "deepseek"],
    editor_model: Literal["deepseek-v4-flash", "deepseek-v4-pro"],
    reviewer_model: Literal["deepseek-v4-flash", "deepseek-v4-pro"],
    settings: Settings,
    api_key: str,
) -> dict[str, Any]:
    provider = build_realistic_provider(
        provider_name=provider_name,
        settings=settings,
        api_key=api_key,
        model=editor_model,
    )
    reviewer_provider = build_realistic_provider(
        provider_name=provider_name,
        settings=settings,
        api_key=api_key,
        model=reviewer_model,
    )
    parent_report = await execute_e2e(
        fixture=fixture,
        paths=paths.parent,
        provider=provider,
        provider_name=provider_name,
        settings=settings,
        secret_values=(api_key,),
        quality_review=True,
        reviewer_provider=reviewer_provider,
    )
    parent_run_id = str(parent_report["final_run"]["id"])
    revision = await _continue_with_revision(
        parent_run_id=parent_run_id,
        paths=paths,
        fixture=fixture,
        provider_name=provider_name,
        provider=provider,
        reviewer_provider=reviewer_provider,
        settings=settings,
        api_key=api_key,
    )
    workflow_passed = bool(parent_report["passed"]) and not revision["workflow_failures"]
    content_acceptance_passed = not revision["content_failures"]
    report = {
        "event": "length_recovery_e2e.completed",
        "passed": workflow_passed and content_acceptance_passed,
        "workflow_passed": workflow_passed,
        "content_acceptance_passed": content_acceptance_passed,
        "failures": [
            *(f"parent.{name}" for name in parent_report["failures"]),
            *(f"workflow.{name}" for name in revision["workflow_failures"]),
            *(f"content.{name}" for name in revision["content_failures"]),
        ],
        "fixture": parent_report["fixture"],
        "creative_brief": parent_report["creative_brief"],
        "runtime": {
            "provider": provider_name,
            "editor_model": provider.model,
            "reviewer_model": reviewer_provider.model,
            "database_path": str(paths.database),
            "hidden_retry": False,
            "model_call_ceiling": 7,
        },
        "parent_flow": parent_report,
        **revision,
    }
    safety = {
        "event": "length_recovery_e2e.safety",
        "passed": revision["workflow_checks"]["logs_structured_and_redacted"],
        "synthetic_source_only": True,
        "source_sample_prompt_and_key_absent": revision["logs"][
            "source_sample_prompt_and_key_absent"
        ],
        "child_status": revision["child"]["status"],
        "child_terminal_error_code": revision["child"].get("terminal_error_code"),
        "model_call_count": revision["usage"]["total"]["model_call_count"],
        "hidden_retry": False,
        "automatic_revision_count": 0,
        "explicit_revision_count": 1,
    }
    _write_report(paths.safety_report, safety)
    report["outputs"]["safety_report"] = str(paths.safety_report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the realistic synthetic persona from Sources through one explicit, "
            "evidence-grounded length-recovery Revision. Dry-run is read-only."
        )
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--provider", choices=("fake", "deepseek"), default="fake")
    parser.add_argument(
        "--editor-model",
        choices=("deepseek-v4-flash", "deepseek-v4-pro"),
        default="deepseek-v4-flash",
    )
    parser.add_argument(
        "--reviewer-model",
        choices=("deepseek-v4-flash", "deepseek-v4-pro"),
        default="deepseek-v4-pro",
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def _print(value: dict[str, Any], *, stream: Any | None = None) -> None:
    print(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        file=sys.stdout if stream is None else stream,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = LengthRecoveryPaths(
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
        _print(
            {
                "event": "length_recovery_e2e.blocked",
                "stage": error.stage,
                "error_code": error.code,
            },
            stream=sys.stderr,
        )
        return 2
    _print(
        build_preflight(
            execute=args.execute,
            provider=args.provider,
            editor_model=args.editor_model,
            reviewer_model=args.reviewer_model,
            api_key_present=bool(api_key),
            fixture=fixture,
            paths=paths,
        )
    )
    if not args.execute:
        return 0
    if args.provider == "deepseek" and not api_key:
        _print(
            {
                "event": "length_recovery_e2e.blocked",
                "stage": "provider",
                "error_code": "deepseek_api_key_missing",
            },
            stream=sys.stderr,
        )
        return 2
    try:
        report = asyncio.run(
            execute_length_recovery_e2e(
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
        report = {
            "event": "length_recovery_e2e.crashed",
            "passed": False,
            "stage": getattr(error, "stage", "unexpected"),
            "error_code": getattr(error, "code", type(error).__name__),
            "message": "Inspect only the sanitized report and runtime logs.",
            "paths": {
                "database": str(paths.database),
                "output_dir": str(paths.output_dir),
                "parent_runtime_log": str(paths.parent.log),
                "revision_runtime_log": str(paths.revision_log),
            },
            "evidence": getattr(error, "safe_context", {}),
        }
        _write_report(paths.report, report)
        _print(report, stream=sys.stderr)
        return 1
    _write_report(paths.report, report)
    _print(
        {
            "event": report["event"],
            "passed": report["passed"],
            "workflow_passed": report["workflow_passed"],
            "content_acceptance_passed": report["content_acceptance_passed"],
            "failures": report["failures"],
            "parent_run_id": report["parent"]["id"],
            "child_run_id": report["child"]["id"],
            "usage": report["usage"]["total"],
            "report_path": str(paths.report),
        }
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
