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
from pydantic import ValidationError

from epiphany.checkpoint_e2e import (
    BACKEND_DIR,
    E2EFlowError,
    _assert_database_has_no_active_tasks,
    _cost_summary,
    _event_summary,
    _forbidden_log_fragments,
    _get_run,
    _import_source,
    _podcast_draft_markdown_checks,
    _poll_for_checkpoint,
    _poll_for_terminal,
    _print_json,
    _request_json,
    _request_markdown,
    _safe_run_summary,
    _scaffold_markdown_checks,
    _show_notes_markdown_checks,
    _write_report,
    build_provider,
    load_fixture,
)
from epiphany.config import Settings
from epiphany.live_deepseek_smoke import database_url_for_path, migrate_database
from epiphany.main import create_app
from epiphany.material_readiness import MaterialReadinessReport, assess_material_readiness
from epiphany.observability import JsonFormatter, RequestContextFilter
from epiphany.quality_contract_schemas import CreativeBrief
from epiphany.runtime.providers import ModelProvider

DEFAULT_FIXTURE_PATH = BACKEND_DIR / "fixtures/e2e/m3-3-quality-contract.zh-CN.json"
DEFAULT_DATABASE_PATH = BACKEND_DIR / "data/quality-contract-e2e.db"
DEFAULT_OUTPUT_DIR = BACKEND_DIR / "artifacts/quality-contract-e2e"

LIVE_MODEL = "deepseek-v4-flash"
MAX_MODEL_CALLS = 4
MAX_TASK_ATTEMPTS = 1
MAX_CONCURRENCY = 1
TASK_TIMEOUT_SECONDS = 120
FLOW_TIMEOUT_SECONDS = 420

READINESS_ARTIFACT_KIND = "material_readiness_report"
EDITOR_TASK_KIND = "build_podcast_draft"
EDITOR_ARTIFACT_KIND = "build_podcast_draft_result"
READINESS_CHECKPOINT = "material_readiness"


@dataclass(frozen=True)
class QualityContractPaths:
    fixture: Path
    database: Path
    output_dir: Path

    @property
    def log(self) -> Path:
        return self.output_dir / "runtime.jsonl"

    @property
    def report(self) -> Path:
        return self.output_dir / "report.json"

    @property
    def readiness_before(self) -> Path:
        return self.output_dir / "material-readiness-before.json"

    @property
    def readiness_after(self) -> Path:
        return self.output_dir / "material-readiness-after.json"

    @property
    def interview_scaffold(self) -> Path:
        return self.output_dir / "interview-scaffold.md"

    @property
    def podcast_draft(self) -> Path:
        return self.output_dir / "podcast-draft.md"

    @property
    def show_notes(self) -> Path:
        return self.output_dir / "show-notes.md"


def _expected_contract(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise E2EFlowError(stage="fixture", code="fixture_expected_invalid")
    required_strings = {
        "initial_readiness_status",
        "final_readiness_status",
        "waiting_status",
        "waiting_step",
        "workflow_version",
    }
    required_counts = {
        "initial_task_count",
        "initial_artifact_count",
        "initial_model_call_count",
        "final_task_count",
        "final_artifact_count",
        "final_model_call_count",
    }
    if any(
        not isinstance(payload.get(field), str) or not str(payload[field]).strip()
        for field in required_strings
    ):
        raise E2EFlowError(stage="fixture", code="fixture_expected_invalid")
    if any(
        not isinstance(payload.get(field), int) or int(payload[field]) < 0
        for field in required_counts
    ):
        raise E2EFlowError(stage="fixture", code="fixture_expected_invalid")
    if payload["initial_readiness_status"] != "needs_more_material":
        raise E2EFlowError(stage="fixture", code="fixture_initial_readiness_invalid")
    if payload["final_readiness_status"] != "ready":
        raise E2EFlowError(stage="fixture", code="fixture_final_readiness_invalid")
    return dict(payload)


def _fixture_segments(
    sources: list[dict[str, Any]],
    *,
    prefix: str,
) -> list[dict[str, str]]:
    """Create stable ephemeral segments for fixture preflight calculations."""

    return [
        {
            "source_id": f"src_fixture_{prefix}_{source_index}",
            "source_segment_id": f"seg_fixture_{prefix}_{source_index}",
            "text": str(source["text"]),
        }
        for source_index, source in enumerate(sources, start=1)
    ]


def load_quality_contract_fixture(path: Path) -> dict[str, Any]:
    fixture = load_fixture(path)
    try:
        creative_brief = CreativeBrief.model_validate(fixture.get("creative_brief")).model_dump(
            mode="json"
        )
    except (ValidationError, TypeError) as error:
        raise E2EFlowError(
            stage="fixture",
            code="fixture_creative_brief_invalid",
        ) from error
    expected = _expected_contract(fixture.get("expected"))

    initial_segments = _fixture_segments(
        fixture["initial_sources"],
        prefix="initial",
    )
    supplemental_segments = _fixture_segments(
        [fixture["supplemental_source"]],
        prefix="supplemental",
    )
    initial_report = assess_material_readiness(
        creative_brief=creative_brief,
        initial_source_segments=initial_segments,
        supplemental_source_segments=[],
    )
    final_report = assess_material_readiness(
        creative_brief=creative_brief,
        initial_source_segments=initial_segments,
        supplemental_source_segments=supplemental_segments,
    )
    if initial_report.status != expected["initial_readiness_status"]:
        raise E2EFlowError(
            stage="fixture",
            code="fixture_does_not_start_below_readiness_threshold",
        )
    if final_report.status != expected["final_readiness_status"]:
        raise E2EFlowError(
            stage="fixture",
            code="fixture_does_not_cross_readiness_threshold",
        )

    return {
        **fixture,
        "creative_brief": creative_brief,
        "expected": expected,
        "raw_fixture_readiness": {
            "initial": initial_report.model_dump(mode="json"),
            "final": final_report.model_dump(mode="json"),
        },
    }


def build_preflight(
    *,
    execute: bool,
    provider: Literal["fake", "deepseek"],
    api_key_present: bool,
    paths: QualityContractPaths,
    billing_currency: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    deepseek_execution = execute and provider == "deepseek"
    brief = fixture["creative_brief"]
    return {
        "event": "quality_contract_e2e.preflight",
        "mode": "execute" if execute else "dry-run",
        "execute_requested": execute,
        "provider": provider,
        "model": LIVE_MODEL if provider == "deepseek" else "fake-v1",
        "network_enabled": deepseek_execution and api_key_present,
        "paid_api_call_possible": deepseek_execution and api_key_present,
        "api_key_status": "present" if api_key_present else "absent",
        "synthetic_source_only": True,
        "fixture_id": fixture["fixture_id"],
        "initial_source_count": len(fixture["initial_sources"]),
        "supplemental_source_count": 1,
        "creative_brief": {
            "target_duration_minutes": brief["target_duration_minutes"],
            "speaking_rate_chars_per_minute": brief["speaking_rate_chars_per_minute"],
            "scenario": brief["scenario"],
            "tone": brief["tone"],
        },
        "raw_fixture_readiness_estimate": {
            "scope": "all synthetic fixture segments before scaffold grounding",
            "initial_status": fixture["raw_fixture_readiness"]["initial"]["status"],
            "final_status": fixture["raw_fixture_readiness"]["final"]["status"],
            "initial_available_chars": fixture["raw_fixture_readiness"]["initial"]["counts"][
                "available_source_char_count"
            ],
            "final_available_chars": fixture["raw_fixture_readiness"]["final"]["counts"][
                "available_source_char_count"
            ],
            "target_chars_min": fixture["raw_fixture_readiness"]["final"][
                "target_script_chars_min"
            ],
        },
        "max_model_calls_per_run": MAX_MODEL_CALLS,
        "max_attempts_per_task": MAX_TASK_ATTEMPTS,
        "max_concurrency": MAX_CONCURRENCY,
        "flow_timeout_seconds": FLOW_TIMEOUT_SECONDS,
        "billing_currency": billing_currency if provider == "deepseek" else "USD",
        "expected_cost": (
            {
                "currency": billing_currency,
                "planning_ceiling": "0.08",
                "is_estimate": True,
                "hard_currency_limit_enforced": False,
            }
            if provider == "deepseek"
            else {
                "currency": "USD",
                "planning_ceiling": "0",
                "is_estimate": False,
                "hard_currency_limit_enforced": True,
            }
        ),
        "paths": {
            "fixture": str(paths.fixture),
            "database": str(paths.database),
            "log": str(paths.log),
            "report": str(paths.report),
            "material_readiness_before": str(paths.readiness_before),
            "material_readiness_after": str(paths.readiness_after),
            "interview_scaffold": str(paths.interview_scaffold),
            "podcast_draft": str(paths.podcast_draft),
            "show_notes": str(paths.show_notes),
        },
        "m3_3_boundary": (
            "The deterministic readiness gate pauses without an Editor call, "
            "survives an application restart, then accepts one synthetic "
            "supplement and continues through the existing Editor."
        ),
    }


def _runtime_settings(
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
        worker_max_concurrency=MAX_CONCURRENCY,
        worker_lease_seconds=150,
        task_timeout_seconds=TASK_TIMEOUT_SECONDS,
        task_max_attempts=MAX_TASK_ATTEMPTS,
        model_max_calls_per_run=MAX_MODEL_CALLS,
        model_provider=provider_name,
        deepseek_billing_currency=settings.deepseek_billing_currency,
    )


def _readiness_artifacts(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        artifact
        for artifact in run.get("artifacts", [])
        if artifact.get("kind") == READINESS_ARTIFACT_KIND
    ]


def _readiness_content(artifact: dict[str, Any]) -> dict[str, Any]:
    content = artifact.get("content_json")
    if not isinstance(content, dict):
        raise E2EFlowError(
            stage="readiness_validation",
            code="readiness_artifact_content_invalid",
        )
    sanitized = {key: value for key, value in content.items() if key != "_execution"}
    try:
        return MaterialReadinessReport.model_validate(sanitized).model_dump(mode="json")
    except ValidationError as error:
        raise E2EFlowError(
            stage="readiness_validation",
            code="readiness_artifact_schema_invalid",
        ) from error


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _is_ordered_subsequence(
    sequence: list[str],
    expected: Sequence[str],
) -> bool:
    cursor = iter(sequence)
    return all(any(item == expected_item for item in cursor) for expected_item in expected)


def _generated_text_fragments(*markdown_documents: str) -> list[str]:
    fragments: set[str] = set()
    for document in markdown_documents:
        if document.strip():
            fragments.add(document)
        for line in document.splitlines():
            cleaned = line.strip()
            if len(cleaned) < 24:
                continue
            fragments.add(cleaned)
            for start in range(0, len(cleaned), 32):
                chunk = cleaned[start : start + 48]
                if len(chunk) >= 24:
                    fragments.add(chunk)
    return sorted(fragments, key=lambda value: (-len(value), value))


def _read_log_summary(
    path: Path,
    *,
    forbidden_texts: list[str],
    provider_name: str,
) -> tuple[dict[str, Any], bool]:
    raw_log = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for line in raw_log.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise E2EFlowError(
                stage="log_validation",
                code="log_json_invalid",
            ) from error
        if not isinstance(row, dict):
            raise E2EFlowError(stage="log_validation", code="log_row_invalid")
        rows.append(row)

    event_counts = Counter(str(row["event"]) for row in rows if row.get("event"))
    required_events = {
        "run.waiting_for_user",
        "run.resume.accepted",
        "run.resume.idempotent_replay",
        "workflow.editor.queued",
        "workflow.editor.completed",
    }
    required_present = required_events.issubset(event_counts)
    deepseek_completed = event_counts.get("provider.deepseek.request.completed", 0)
    deepseek_count_valid = (
        deepseek_completed == MAX_MODEL_CALLS
        if provider_name == "deepseek"
        else deepseek_completed == 0
    )
    content_redacted = all(fragment not in raw_log for fragment in forbidden_texts)
    summary = {
        "path": str(path),
        "line_count": len(rows),
        "all_lines_are_json": True,
        "event_counts": dict(sorted(event_counts.items())),
        "required_events_present": required_present,
        "deepseek_completed_call_count": deepseek_completed,
        "source_and_generated_text_absent": content_redacted,
    }
    return (
        summary,
        required_present and deepseek_count_valid and content_redacted,
    )


async def _events(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    stage: str,
    request_id: str,
) -> list[dict[str, Any]]:
    payload = await _request_json(
        client,
        "GET",
        f"/runs/{run_id}/events",
        stage=stage,
        request_id=request_id,
        expected_statuses={200},
    )
    if not isinstance(payload, list):
        raise E2EFlowError(stage=stage, code="events_response_invalid")
    return payload


async def execute_e2e(
    *,
    fixture: dict[str, Any],
    paths: QualityContractPaths,
    provider: ModelProvider,
    provider_name: Literal["fake", "deepseek"],
    settings: Settings,
    secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    paths.database.parent.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    database_url = database_url_for_path(paths.database)
    await asyncio.to_thread(migrate_database, database_url)
    await asyncio.to_thread(_assert_database_has_no_active_tasks, paths.database)
    runtime_settings = _runtime_settings(
        database_url=database_url,
        provider_name=provider_name,
        settings=settings,
    )

    file_handler = logging.FileHandler(paths.log, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(RequestContextFilter())
    application_logger = logging.getLogger("epiphany")
    preexisting_handlers = set(application_logger.handlers)
    preexisting_handler_levels = {handler: handler.level for handler in preexisting_handlers}
    previous_level = application_logger.level
    previous_propagate = application_logger.propagate
    application_logger.addHandler(file_handler)

    imported_initial: list[dict[str, Any]]
    waiting: dict[str, Any]
    waiting_events: list[dict[str, Any]]
    scaffold_before: str
    run_id: str
    try:
        first_app = create_app(settings=runtime_settings, provider=provider)
        async with first_app.router.lifespan_context(first_app):
            transport = httpx.ASGITransport(app=first_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://epiphany-quality-contract-e2e",
                timeout=30,
            ) as client:
                imported_initial = [
                    await _import_source(
                        client,
                        source,
                        stage=f"import_initial_source_{index}",
                        request_id=f"req_m33_source_{index}",
                    )
                    for index, source in enumerate(
                        fixture["initial_sources"],
                        start=1,
                    )
                ]
                source_ids = [str(imported["source"]["id"]) for imported in imported_initial]
                created = await _request_json(
                    client,
                    "POST",
                    "/runs",
                    stage="create_run",
                    request_id="req_m33_create_run",
                    expected_statuses={201},
                    json_body={
                        "workflow_type": "episode-research",
                        "payload": {
                            "topic": fixture["topic"],
                            "source_ids": source_ids,
                            "creative_brief": fixture["creative_brief"],
                        },
                    },
                )
                if not isinstance(created, dict) or not isinstance(
                    created.get("id"),
                    str,
                ):
                    raise E2EFlowError(
                        stage="create_run",
                        code="run_response_invalid",
                    )
                run_id = created["id"]
                waiting = await _poll_for_checkpoint(client, run_id)
                if waiting.get("status") != fixture["expected"]["waiting_status"]:
                    raise E2EFlowError(
                        stage="poll_checkpoint",
                        code=f"run_stopped_as_{waiting.get('status', 'unknown')}",
                        safe_context={"run": _safe_run_summary(waiting)},
                    )
                waiting_events = await _events(
                    client,
                    run_id,
                    stage="events_before_restart",
                    request_id="req_m33_events_before_restart",
                )
                scaffold_before = await _request_markdown(
                    client,
                    f"/runs/{run_id}/exports/interview-scaffold.md",
                    stage="export_waiting_scaffold",
                    request_id="req_m33_export_waiting_scaffold",
                    run_id=run_id,
                    filename_prefix="interview-scaffold",
                )

        # This is the product-level durability proof: the first application
        # and Worker are gone before the same SQLite state is reopened.
        await asyncio.to_thread(_assert_database_has_no_active_tasks, paths.database)

        restarted_app = create_app(settings=runtime_settings, provider=provider)
        async with restarted_app.router.lifespan_context(restarted_app):
            transport = httpx.ASGITransport(app=restarted_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://epiphany-quality-contract-e2e",
                timeout=30,
            ) as client:
                restarted_waiting = await _get_run(
                    client,
                    run_id,
                    stage="get_waiting_after_restart",
                    request_id="req_m33_get_after_restart",
                )
                restart_events = await _events(
                    client,
                    run_id,
                    stage="events_after_restart",
                    request_id="req_m33_events_after_restart",
                )

                supplemental = await _import_source(
                    client,
                    fixture["supplemental_source"],
                    stage="import_supplemental_source",
                    request_id="req_m33_source_supplemental",
                )
                supplemental_source_id = str(supplemental["source"]["id"])
                resume_payload = {
                    "checkpoint": READINESS_CHECKPOINT,
                    "submission_id": fixture["submission_id"],
                    "source_ids": [supplemental_source_id],
                }
                resumed = await _request_json(
                    client,
                    "POST",
                    f"/runs/{run_id}/resume",
                    stage="resume",
                    request_id="req_m33_resume",
                    expected_statuses={200},
                    json_body=resume_payload,
                )
                replay = await _request_json(
                    client,
                    "POST",
                    f"/runs/{run_id}/resume",
                    stage="resume_replay",
                    request_id="req_m33_resume_replay",
                    expected_statuses={200},
                    json_body=resume_payload,
                )
                if not isinstance(resumed, dict) or not isinstance(replay, dict):
                    raise E2EFlowError(
                        stage="resume",
                        code="resume_response_invalid",
                    )

                final_run = await _poll_for_terminal(client, run_id)
                if final_run.get("status") != "succeeded":
                    raise E2EFlowError(
                        stage="poll_terminal",
                        code=f"run_stopped_as_{final_run.get('status', 'unknown')}",
                        safe_context={"run": _safe_run_summary(final_run)},
                    )
                final_events = await _events(
                    client,
                    run_id,
                    stage="events_after_editor",
                    request_id="req_m33_events_after_editor",
                )
                scaffold_after = await _request_markdown(
                    client,
                    f"/runs/{run_id}/exports/interview-scaffold.md",
                    stage="export_final_scaffold",
                    request_id="req_m33_export_final_scaffold",
                    run_id=run_id,
                    filename_prefix="interview-scaffold",
                )
                podcast_draft = await _request_markdown(
                    client,
                    f"/runs/{run_id}/exports/podcast-draft.md",
                    stage="export_podcast_draft",
                    request_id="req_m33_export_podcast_draft",
                    run_id=run_id,
                    filename_prefix="podcast-draft",
                )
                show_notes = await _request_markdown(
                    client,
                    f"/runs/{run_id}/exports/show-notes.md",
                    stage="export_show_notes",
                    request_id="req_m33_export_show_notes",
                    run_id=run_id,
                    filename_prefix="show-notes",
                )
    finally:
        application_logger.removeHandler(file_handler)
        file_handler.close()
        for handler in list(application_logger.handlers):
            if handler not in preexisting_handlers and getattr(
                handler,
                "_epiphany_json_handler",
                False,
            ):
                application_logger.removeHandler(handler)
                handler.close()
        for handler, level in preexisting_handler_levels.items():
            handler.setLevel(level)
        application_logger.setLevel(previous_level)
        application_logger.propagate = previous_propagate

    readiness_before_artifacts = _readiness_artifacts(waiting)
    readiness_after_artifacts = _readiness_artifacts(final_run)
    if len(readiness_before_artifacts) != 1:
        raise E2EFlowError(
            stage="readiness_validation",
            code="initial_readiness_artifact_count_invalid",
        )
    if len(readiness_after_artifacts) != 2:
        raise E2EFlowError(
            stage="readiness_validation",
            code="final_readiness_artifact_count_invalid",
        )
    readiness_before = _readiness_content(readiness_before_artifacts[0])
    readiness_after = _readiness_content(readiness_after_artifacts[-1])
    _write_json(paths.readiness_before, readiness_before)
    _write_json(paths.readiness_after, readiness_after)
    paths.interview_scaffold.write_text(scaffold_before, encoding="utf-8")
    paths.podcast_draft.write_text(podcast_draft, encoding="utf-8")
    paths.show_notes.write_text(show_notes, encoding="utf-8")

    expected = fixture["expected"]
    waiting_summary = _safe_run_summary(waiting)
    restarted_summary = _safe_run_summary(restarted_waiting)
    final_summary = _safe_run_summary(final_run)
    waiting_event_types = [str(event.get("type")) for event in waiting_events]
    final_event_types = [str(event.get("type")) for event in final_events]
    scaffold_sha = hashlib.sha256(scaffold_before.encode("utf-8")).hexdigest()
    draft_sha = hashlib.sha256(podcast_draft.encode("utf-8")).hexdigest()
    notes_sha = hashlib.sha256(show_notes.encode("utf-8")).hexdigest()
    model_calls = final_run.get("model_calls", [])
    expected_currency = provider.billing_currency.upper()
    supplemental_title = str(fixture["supplemental_source"]["title"])
    scaffold_structure = _scaffold_markdown_checks(
        scaffold_before,
        topic=fixture["topic"],
    )
    draft_structure = _podcast_draft_markdown_checks(
        podcast_draft,
        topic=fixture["topic"],
        supplemental_title=supplemental_title,
    )
    notes_structure = _show_notes_markdown_checks(
        show_notes,
        topic=fixture["topic"],
        supplemental_title=supplemental_title,
    )

    checks = {
        "initial_sources_imported": len(imported_initial) == 3,
        "creative_brief_persisted": (
            waiting.get("input_json", {}).get("creative_brief") == fixture["creative_brief"]
        ),
        "initial_readiness_needs_material": (
            readiness_before["status"] == expected["initial_readiness_status"]
            and readiness_before["counts"]["supplemental_segment_count"] == 0
            and readiness_before["additional_source_chars_needed"] > 0
            and bool(readiness_before["gaps"])
            and bool(readiness_before["follow_up_questions"])
        ),
        "waiting_checkpoint_reached": (
            waiting.get("workflow_version") == expected["workflow_version"]
            and waiting.get("status") == expected["waiting_status"]
            and waiting.get("current_step") == expected["waiting_step"]
        ),
        "waiting_runtime_counts": (
            len(waiting.get("tasks", [])) == expected["initial_task_count"]
            and len(waiting.get("artifacts", [])) == expected["initial_artifact_count"]
            and len(waiting.get("model_calls", [])) == expected["initial_model_call_count"]
        ),
        "no_editor_before_supplement": (
            all(task.get("kind") != EDITOR_TASK_KIND for task in waiting.get("tasks", []))
            and all(
                artifact.get("kind") != EDITOR_ARTIFACT_KIND
                for artifact in waiting.get("artifacts", [])
            )
            and "workflow.editor.queued" not in waiting_event_types
        ),
        "waiting_state_survived_restart": (
            restarted_summary == waiting_summary and restart_events == waiting_events
        ),
        "supplemental_source_imported": (supplemental["source"]["segment_count"] >= 1),
        "resume_applied_once": (
            resumed.get("resumed") is True and resumed.get("idempotent_replay") is False
        ),
        "resume_replay_idempotent": (
            replay.get("resumed") is False
            and replay.get("idempotent_replay") is True
            and replay.get("submission_artifact_id") == resumed.get("submission_artifact_id")
        ),
        "final_readiness_ready": (
            readiness_after["status"] == expected["final_readiness_status"]
            and readiness_after["counts"]["supplemental_segment_count"] >= 1
            and readiness_after["additional_source_chars_needed"] == 0
            and not readiness_after["gaps"]
        ),
        "final_runtime_counts": (
            len(final_run.get("tasks", [])) == expected["final_task_count"]
            and len(final_run.get("artifacts", [])) == expected["final_artifact_count"]
            and len(final_run.get("model_calls", [])) == expected["final_model_call_count"]
        ),
        "final_status_succeeded": (
            final_run.get("status") == "succeeded" and final_run.get("current_step") == "complete"
        ),
        "model_calls_match_provider": (
            len(model_calls) == MAX_MODEL_CALLS
            and all(
                call.get("status") == "succeeded"
                and call.get("provider") == provider_name
                and call.get("model") == provider.model
                and str(call.get("cost_currency", "")).upper() == expected_currency
                for call in model_calls
            )
        ),
        "pivotal_events_ordered": _is_ordered_subsequence(
            final_event_types,
            [
                "run.waiting_for_user",
                "run.resumed",
                "workflow.editor.queued",
                "workflow.editor.completed",
                "run.succeeded",
            ],
        ),
        "resume_and_editor_emitted_once": (
            final_event_types.count("run.resumed") == 1
            and final_event_types.count("workflow.editor.queued") == 1
            and final_event_types.count("workflow.editor.completed") == 1
        ),
        "scaffold_stable_after_restart_and_resume": (scaffold_before == scaffold_after),
        "scaffold_markdown_structure_valid": all(scaffold_structure.values()),
        "podcast_draft_structure_valid": all(draft_structure.values()),
        "show_notes_structure_valid": all(notes_structure.values()),
        "final_markdown_uses_supplemental_evidence": (
            draft_structure["supplemental_evidence_used_in_body"]
            and notes_structure["supplemental_evidence_used_in_body"]
        ),
    }

    forbidden_texts = [
        *_forbidden_log_fragments(
            fixture,
            secret_values=secret_values,
        ),
        *_generated_text_fragments(
            scaffold_before,
            podcast_draft,
            show_notes,
        ),
    ]
    log_summary, log_checks_passed = _read_log_summary(
        paths.log,
        forbidden_texts=forbidden_texts,
        provider_name=provider_name,
    )
    checks["logs_structured_and_redacted"] = log_checks_passed
    failures = sorted(name for name, passed in checks.items() if not passed)

    return {
        "event": "quality_contract_e2e.completed",
        "passed": not failures,
        "failures": failures,
        "fixture": {
            "id": fixture["fixture_id"],
            "path": str(paths.fixture),
            "synthetic": True,
            "initial_source_count": len(fixture["initial_sources"]),
            "supplemental_source_count": 1,
        },
        "creative_brief": fixture["creative_brief"],
        "runtime": {
            "provider": provider_name,
            "model": provider.model,
            "database_path": str(paths.database),
            "max_model_calls": MAX_MODEL_CALLS,
            "max_attempts_per_task": MAX_TASK_ATTEMPTS,
            "max_concurrency": MAX_CONCURRENCY,
        },
        "waiting_run": waiting_summary,
        "restarted_waiting_run": restarted_summary,
        "final_run": final_summary,
        "readiness": {
            "before": {
                "path": str(paths.readiness_before),
                "artifact_id": readiness_before_artifacts[0]["id"],
                "report": readiness_before,
            },
            "after": {
                "path": str(paths.readiness_after),
                "artifact_id": readiness_after_artifacts[-1]["id"],
                "report": readiness_after,
            },
        },
        "resume": {
            "first_applied": resumed.get("resumed"),
            "replay_idempotent": replay.get("idempotent_replay"),
            "submission_artifact_id": resumed.get("submission_artifact_id"),
        },
        "events": {
            "before_restart": _event_summary(waiting_events),
            "after_restart": _event_summary(restart_events),
            "final": _event_summary(final_events),
        },
        "usage": {
            "input_tokens": sum(int(call.get("input_tokens") or 0) for call in model_calls),
            "output_tokens": sum(int(call.get("output_tokens") or 0) for call in model_calls),
            "duration_ms": sum(int(call.get("duration_ms") or 0) for call in model_calls),
            "estimated_costs": _cost_summary(model_calls),
        },
        "markdown": {
            "interview_scaffold": {
                "path": str(paths.interview_scaffold),
                "char_count": len(scaffold_before),
                "sha256": scaffold_sha,
                "structure": scaffold_structure,
            },
            "podcast_draft": {
                "path": str(paths.podcast_draft),
                "char_count": len(podcast_draft),
                "sha256": draft_sha,
                "structure": draft_structure,
            },
            "show_notes": {
                "path": str(paths.show_notes),
                "char_count": len(show_notes),
                "sha256": notes_sha,
                "structure": notes_structure,
            },
        },
        "logs": log_summary,
        "checks": checks,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise the M3.3 Creative Brief -> insufficient readiness -> "
            "durable restart -> synthetic supplement -> ready -> Editor journey. "
            "Without --execute this command only prints a safe preflight."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the E2E flow; required even for the zero-cost Fake Provider",
    )
    parser.add_argument(
        "--provider",
        choices=("fake", "deepseek"),
        default="fake",
        help="model provider (default: fake)",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE_PATH,
        help="synthetic M3.3 JSON fixture",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help="dedicated ignored SQLite database",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="ignored directory for logs, report, readiness, and Markdown",
    )
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
        fixture = load_quality_contract_fixture(paths.fixture)
    except E2EFlowError as error:
        _print_json(
            {
                "event": "quality_contract_e2e.blocked",
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
            api_key_present=bool(api_key),
            paths=paths,
            billing_currency=settings.deepseek_billing_currency,
            fixture=fixture,
        )
    )
    if not args.execute:
        return 0
    if args.provider == "deepseek" and not api_key:
        _print_json(
            {
                "event": "quality_contract_e2e.blocked",
                "stage": "provider",
                "error_code": "deepseek_api_key_missing",
                "message": (
                    "Add EPIPHANY_DEEPSEEK_API_KEY to backend/.env, "
                    "then rerun with --provider deepseek --execute."
                ),
            },
            stream=sys.stderr,
        )
        return 2

    try:
        try:
            provider = build_provider(
                provider_name=args.provider,
                settings=settings,
                api_key=api_key,
            )
        except (TypeError, ValueError) as error:
            raise E2EFlowError(
                stage="provider",
                code="provider_configuration_invalid",
            ) from error
        report = asyncio.run(
            execute_e2e(
                fixture=fixture,
                paths=paths,
                provider=provider,
                provider_name=args.provider,
                settings=settings,
                secret_values=(api_key,),
            )
        )
    except Exception as error:
        paths.output_dir.mkdir(parents=True, exist_ok=True)
        failure_report = {
            "event": "quality_contract_e2e.crashed",
            "passed": False,
            "stage": getattr(error, "stage", "unexpected"),
            "error_code": getattr(error, "code", type(error).__name__),
            "message": ("Inspect the sanitized report, runtime log, and dedicated database."),
            "paths": {
                "fixture": str(paths.fixture),
                "database": str(paths.database),
                "log": str(paths.log),
                "report": str(paths.report),
                "material_readiness_before": str(paths.readiness_before),
                "material_readiness_after": str(paths.readiness_after),
                "interview_scaffold": str(paths.interview_scaffold),
                "podcast_draft": str(paths.podcast_draft),
                "show_notes": str(paths.show_notes),
            },
            "evidence": getattr(error, "safe_context", {}),
        }
        _write_report(paths.report, failure_report)
        _print_json(failure_report, stream=sys.stderr)
        return 1

    _write_report(paths.report, report)
    _print_json(
        {
            "event": report["event"],
            "passed": report["passed"],
            "failures": report["failures"],
            "run_id": report["final_run"]["id"],
            "provider": report["runtime"]["provider"],
            "model": report["runtime"]["model"],
            "input_tokens": report["usage"]["input_tokens"],
            "output_tokens": report["usage"]["output_tokens"],
            "estimated_costs": report["usage"]["estimated_costs"],
            "database_path": report["runtime"]["database_path"],
            "log_path": report["logs"]["path"],
            "report_path": str(paths.report),
            "material_readiness_before_path": str(paths.readiness_before),
            "material_readiness_after_path": str(paths.readiness_after),
            "interview_scaffold_path": str(paths.interview_scaffold),
            "podcast_draft_path": str(paths.podcast_draft),
            "show_notes_path": str(paths.show_notes),
        }
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
