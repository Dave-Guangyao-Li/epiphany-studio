from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from time import monotonic
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from epiphany.config import Settings
from epiphany.live_deepseek_smoke import database_url_for_path, migrate_database
from epiphany.main import create_app
from epiphany.observability import JsonFormatter, RequestContextFilter
from epiphany.runtime.providers import DeepSeekProvider, FakeProvider, ModelProvider
from epiphany.schemas import CreateSourceRequest

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_PATH = BACKEND_DIR / "fixtures/e2e/m3-1-episode.zh-CN.json"
DEFAULT_DATABASE_PATH = BACKEND_DIR / "data/checkpoint-e2e.db"
DEFAULT_OUTPUT_DIR = BACKEND_DIR / "artifacts/checkpoint-e2e"

LIVE_MODEL = "deepseek-v4-flash"
MAX_MODEL_CALLS = 3
MAX_TASK_ATTEMPTS = 1
MAX_CONCURRENCY = 1
MAX_OUTPUT_TOKENS_PER_CALL = 3_200
MAX_SOURCE_CHARS = 8_000
MAX_INTERVIEW_BUNDLE_CHARS = 24_000
TASK_TIMEOUT_SECONDS = 120
FLOW_TIMEOUT_SECONDS = 420
POLL_INTERVAL_SECONDS = 1.0

EXPECTED_WAITING_STATUS = "waiting_for_user"
EXPECTED_WAITING_STEP = "awaiting_interview_response"
EXPECTED_FINAL_STATUS = "succeeded"
EXPECTED_FINAL_STEP = "complete"
EXPECTED_WORKFLOW_VERSION = "v3"
EXPECTED_PRE_RESUME_ARTIFACT_KINDS = {
    "timeline_research_result",
    "theme_research_result",
    "episode_research_bundle",
    "build_interview_scaffold_result",
}
EXPECTED_FINAL_ARTIFACT_KINDS = EXPECTED_PRE_RESUME_ARTIFACT_KINDS | {"user_material_submission"}


class E2EFlowError(RuntimeError):
    def __init__(
        self,
        *,
        stage: str,
        code: str,
        safe_context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.stage = stage
        self.code = code
        self.safe_context = safe_context or {}


@dataclass(frozen=True)
class E2EPaths:
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
    def markdown(self) -> Path:
        return self.output_dir / "interview-scaffold.md"


def build_preflight(
    *,
    execute: bool,
    provider: Literal["fake", "deepseek"],
    api_key_present: bool,
    paths: E2EPaths,
    billing_currency: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    deepseek_execution = execute and provider == "deepseek"
    return {
        "event": "checkpoint_e2e.preflight",
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
        "max_model_calls_per_run": MAX_MODEL_CALLS,
        "max_attempts_per_task": MAX_TASK_ATTEMPTS,
        "max_concurrency": MAX_CONCURRENCY,
        "max_output_tokens_per_call": (MAX_OUTPUT_TOKENS_PER_CALL if provider == "deepseek" else 0),
        "max_research_source_chars": MAX_SOURCE_CHARS,
        "max_interview_bundle_chars": MAX_INTERVIEW_BUNDLE_CHARS,
        "flow_timeout_seconds": FLOW_TIMEOUT_SECONDS,
        "billing_currency": billing_currency if provider == "deepseek" else "USD",
        "expected_cost": {
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
        },
        "paths": {
            "fixture": str(paths.fixture),
            "database": str(paths.database),
            "log": str(paths.log),
            "report": str(paths.report),
            "markdown": str(paths.markdown),
        },
        "m3_1_boundary": (
            "The exported Markdown is an interview scaffold. "
            "M3.2 Editor will turn supplemental material into a final podcast draft."
        ),
    }


def load_fixture(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise E2EFlowError(stage="fixture", code="fixture_unreadable") from error
    if not isinstance(payload, dict):
        raise E2EFlowError(stage="fixture", code="fixture_root_invalid")

    required_text_fields = ("fixture_id", "topic", "submission_id")
    if any(
        not isinstance(payload.get(field), str) or not payload[field].strip()
        for field in required_text_fields
    ):
        raise E2EFlowError(stage="fixture", code="fixture_required_field_invalid")
    initial_sources = payload.get("initial_sources")
    if not isinstance(initial_sources, list) or len(initial_sources) != 3:
        raise E2EFlowError(stage="fixture", code="fixture_initial_sources_invalid")
    supplemental_source = payload.get("supplemental_source")
    if not isinstance(supplemental_source, dict):
        raise E2EFlowError(stage="fixture", code="fixture_supplemental_source_invalid")

    try:
        validated_initial = [
            CreateSourceRequest.model_validate(source).model_dump(mode="json")
            for source in initial_sources
        ]
        validated_supplemental = CreateSourceRequest.model_validate(supplemental_source).model_dump(
            mode="json"
        )
    except ValidationError as error:
        raise E2EFlowError(stage="fixture", code="fixture_source_invalid") from error

    all_sources = [*validated_initial, validated_supplemental]
    if any(
        source["metadata"].get("synthetic") is not True
        or source["metadata"].get("contains_personal_data") is not False
        for source in all_sources
    ):
        raise E2EFlowError(stage="fixture", code="fixture_privacy_marker_invalid")

    return {
        **payload,
        "initial_sources": validated_initial,
        "supplemental_source": validated_supplemental,
    }


def build_provider(
    *,
    provider_name: Literal["fake", "deepseek"],
    settings: Settings,
    api_key: str,
) -> ModelProvider:
    if provider_name == "fake":
        return FakeProvider()
    return DeepSeekProvider(
        api_key=api_key,
        model=LIVE_MODEL,
        billing_currency=settings.deepseek_billing_currency,
        base_url=settings.deepseek_base_url,
        max_tokens=MAX_OUTPUT_TOKENS_PER_CALL,
        max_source_chars=MAX_SOURCE_CHARS,
        max_interview_bundle_chars=MAX_INTERVIEW_BUNDLE_CHARS,
        request_timeout_seconds=TASK_TIMEOUT_SECONDS + 5,
    )


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    stage: str,
    request_id: str,
    expected_statuses: set[int],
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    try:
        response = await client.request(
            method,
            path,
            headers={"X-Request-ID": request_id},
            json=json_body,
        )
    except httpx.HTTPError as error:
        raise E2EFlowError(
            stage=stage,
            code="http_request_failed",
            safe_context={"method": method, "path": path},
        ) from error
    if response.status_code not in expected_statuses:
        raise E2EFlowError(
            stage=stage,
            code=f"unexpected_http_status_{response.status_code}",
        )
    if response.headers.get("X-Request-ID") != request_id:
        raise E2EFlowError(
            stage=stage,
            code="response_request_id_mismatch",
            safe_context={
                "expected_request_id": request_id,
                "actual_request_id": response.headers.get("X-Request-ID"),
            },
        )
    try:
        payload = response.json()
    except json.JSONDecodeError as error:
        raise E2EFlowError(stage=stage, code="response_json_invalid") from error
    if not isinstance(payload, (dict, list)):
        raise E2EFlowError(stage=stage, code="response_shape_invalid")
    return payload


async def _request_markdown(
    client: httpx.AsyncClient,
    path: str,
    *,
    stage: str,
    request_id: str,
    run_id: str,
) -> str:
    try:
        response = await client.get(
            path,
            headers={"X-Request-ID": request_id},
        )
    except httpx.HTTPError as error:
        raise E2EFlowError(
            stage=stage,
            code="http_request_failed",
            safe_context={"method": "GET", "path": path},
        ) from error
    if response.status_code != 200:
        raise E2EFlowError(
            stage=stage,
            code=f"unexpected_http_status_{response.status_code}",
        )
    if response.headers.get("X-Request-ID") != request_id:
        raise E2EFlowError(
            stage=stage,
            code="response_request_id_mismatch",
            safe_context={
                "expected_request_id": request_id,
                "actual_request_id": response.headers.get("X-Request-ID"),
            },
        )
    if not response.headers.get("Content-Type", "").lower().startswith("text/markdown"):
        raise E2EFlowError(stage=stage, code="markdown_content_type_invalid")
    expected_disposition = f'attachment; filename="interview-scaffold-{run_id}.md"'
    if response.headers.get("Content-Disposition") != expected_disposition:
        raise E2EFlowError(stage=stage, code="markdown_content_disposition_invalid")
    return response.text


async def _import_source(
    client: httpx.AsyncClient,
    source: dict[str, Any],
    *,
    stage: str,
    request_id: str,
) -> dict[str, Any]:
    payload = await _request_json(
        client,
        "POST",
        "/sources",
        stage=stage,
        request_id=request_id,
        expected_statuses={200, 201},
        json_body=source,
    )
    if not isinstance(payload, dict):
        raise E2EFlowError(stage=stage, code="source_response_invalid")
    return payload


async def _get_run(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    stage: str,
    request_id: str,
) -> dict[str, Any]:
    payload = await _request_json(
        client,
        "GET",
        f"/runs/{run_id}",
        stage=stage,
        request_id=request_id,
        expected_statuses={200},
    )
    if not isinstance(payload, dict):
        raise E2EFlowError(stage=stage, code="run_response_invalid")
    return payload


async def _poll_for_checkpoint(
    client: httpx.AsyncClient,
    run_id: str,
) -> dict[str, Any]:
    deadline = monotonic() + FLOW_TIMEOUT_SECONDS
    poll_index = 0
    while monotonic() < deadline:
        poll_index += 1
        run = await _get_run(
            client,
            run_id,
            stage="poll_checkpoint",
            request_id=f"req_e2e_poll_{poll_index:04d}",
        )
        if run.get("status") in {
            EXPECTED_WAITING_STATUS,
            "succeeded",
            "failed",
            "cancelled",
        }:
            return run
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    raise E2EFlowError(stage="poll_checkpoint", code="flow_timeout")


def _safe_run_summary(run: dict[str, Any]) -> dict[str, Any]:
    tasks = run.get("tasks", [])
    artifacts = run.get("artifacts", [])
    model_calls = run.get("model_calls", [])
    return {
        "id": run.get("id"),
        "workflow_type": run.get("workflow_type"),
        "workflow_version": run.get("workflow_version"),
        "status": run.get("status"),
        "current_step": run.get("current_step"),
        "output_artifact_id": run.get("output_artifact_id"),
        "model_call_count": run.get("model_call_count"),
        "task_count": len(tasks),
        "artifact_count": len(artifacts),
        "model_calls_recorded": len(model_calls),
        "task_statuses": dict(sorted(Counter(task.get("status") for task in tasks).items())),
        "task_kinds": sorted(task.get("kind") for task in tasks),
        "artifact_kinds": sorted(artifact.get("kind") for artifact in artifacts),
        "tasks": [
            {
                "id": task.get("id"),
                "kind": task.get("kind"),
                "status": task.get("status"),
                "attempt": task.get("attempt"),
                "max_attempts": task.get("max_attempts"),
                "output_artifact_id": task.get("output_artifact_id"),
                "error_code": task.get("error_code"),
            }
            for task in tasks
        ],
        "model_calls": [
            {
                "id": call.get("id"),
                "task_id": call.get("task_id"),
                "attempt": call.get("attempt"),
                "provider": call.get("provider"),
                "model": call.get("model"),
                "status": call.get("status"),
                "input_tokens": call.get("input_tokens"),
                "output_tokens": call.get("output_tokens"),
                "duration_ms": call.get("duration_ms"),
                "estimated_cost_micros": call.get("estimated_cost_micros"),
                "cost_currency": call.get("cost_currency"),
                "error_code": call.get("error_code"),
            }
            for call in model_calls
        ],
    }


def _event_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    event_types = [str(event.get("type")) for event in events]
    return {
        "count": len(events),
        "types": event_types,
        "type_counts": dict(sorted(Counter(event_types).items())),
    }


def _cost_summary(model_calls: list[dict[str, Any]]) -> dict[str, Any]:
    micros_by_currency: dict[str, int] = {}
    for call in model_calls:
        currency = str(call.get("cost_currency", "USD")).upper()
        micros = int(call.get("estimated_cost_micros") or 0)
        micros_by_currency[currency] = micros_by_currency.get(currency, 0) + micros
    return {
        currency: {
            "micros": micros,
            "amount": f"{Decimal(micros) / Decimal(1_000_000):.6f}",
        }
        for currency, micros in sorted(micros_by_currency.items())
    }


def _forbidden_log_fragments(
    fixture: dict[str, Any],
    *,
    secret_values: Sequence[str] = (),
) -> list[str]:
    """Build leak detectors without copying source material into reports."""

    fragments = {"Authorization", "Bearer "}
    sources = [*fixture["initial_sources"], fixture["supplemental_source"]]
    for source in sources:
        text = str(source["text"]).strip()
        if not text:
            continue
        fragments.add(text)
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\n", text)]
        for paragraph in paragraphs:
            if len(paragraph) < 16:
                continue
            fragments.add(paragraph)
            for start in range(0, len(paragraph), 32):
                chunk = paragraph[start : start + 48]
                if len(chunk) >= 16:
                    fragments.add(chunk)
    fragments.update(secret.strip() for secret in secret_values if secret.strip())
    return sorted(fragments, key=lambda value: (-len(value), value))


def _assert_database_has_no_active_tasks(path: Path) -> None:
    """Refuse to start a Worker that could claim tasks from an earlier run."""

    try:
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA query_only = ON")
            active_task_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
            )
    except sqlite3.Error as error:
        raise E2EFlowError(
            stage="database_preflight",
            code="database_active_task_check_failed",
        ) from error
    if active_task_count:
        raise E2EFlowError(
            stage="database_preflight",
            code="database_has_active_tasks",
            safe_context={"active_task_count": active_task_count},
        )


def _read_log_summary(
    path: Path,
    *,
    forbidden_texts: list[str],
    provider_name: str,
) -> tuple[dict[str, Any], bool]:
    rows: list[dict[str, Any]] = []
    raw_log = path.read_text(encoding="utf-8")
    for line in raw_log.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise E2EFlowError(stage="log_validation", code="log_json_invalid") from error
        if not isinstance(row, dict):
            raise E2EFlowError(stage="log_validation", code="log_row_invalid")
        rows.append(row)

    event_counts = Counter(str(row["event"]) for row in rows if row.get("event"))
    error_code_counts = Counter(str(row["error_code"]) for row in rows if row.get("error_code"))
    required_events = {
        "run.waiting_for_user",
        "run.resume.accepted",
        "run.resume.idempotent_replay",
    }
    required_present = required_events.issubset(event_counts)
    deepseek_count_valid = (
        event_counts.get("provider.deepseek.request.completed", 0) == MAX_MODEL_CALLS
        if provider_name == "deepseek"
        else event_counts.get("provider.deepseek.request.completed", 0) == 0
    )
    redacted = all(text not in raw_log for text in forbidden_texts)
    summary = {
        "path": str(path),
        "line_count": len(rows),
        "all_lines_are_json": True,
        "event_counts": dict(sorted(event_counts.items())),
        "error_code_counts": dict(sorted(error_code_counts.items())),
        "required_events_present": required_present,
        "deepseek_completed_call_count": event_counts.get("provider.deepseek.request.completed", 0),
        "source_text_absent": redacted,
    }
    return summary, required_present and deepseek_count_valid and redacted


def _markdown_checks(markdown: str, *, topic: str) -> dict[str, bool]:
    numbered_sections = re.findall(r"^## \d+\.", markdown, flags=re.MULTILINE)
    return {
        "title_matches_topic": markdown.splitlines()[0] == f"# {topic}",
        "has_opening": "## 开场" in markdown,
        "has_at_least_two_sections": len(numbered_sections) >= 2,
        "has_interview_questions": "### 采访问题" in markdown,
        "has_source_labels": (
            "来源：[S" in markdown
            and "## 来源索引" in markdown
            and re.search(r"src_[^\s`]+#seg_[^\s`]+", markdown) is None
        ),
    }


async def execute_e2e(
    *,
    fixture: dict[str, Any],
    paths: E2EPaths,
    provider: ModelProvider,
    provider_name: Literal["fake", "deepseek"],
    settings: Settings,
    secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    paths.database.parent.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    database_url = database_url_for_path(paths.database)
    # Alembic's async environment owns its own event loop. Run it in a worker
    # thread so this async E2E coordinator never nests asyncio.run().
    await asyncio.to_thread(migrate_database, database_url)
    await asyncio.to_thread(_assert_database_has_no_active_tasks, paths.database)

    runtime_settings = Settings(
        database_url=database_url,
        create_schema_on_start=False,
        # E2E evidence must include lifecycle INFO events even when the
        # developer's normal .env intentionally suppresses them.
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
    app = create_app(settings=runtime_settings, provider=provider)

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

    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://epiphany-e2e",
                timeout=30,
            ) as client:
                imported_initial = [
                    await _import_source(
                        client,
                        source,
                        stage=f"import_initial_source_{index}",
                        request_id=f"req_e2e_source_{index}",
                    )
                    for index, source in enumerate(fixture["initial_sources"], start=1)
                ]
                initial_source_ids = [
                    str(imported["source"]["id"]) for imported in imported_initial
                ]

                created = await _request_json(
                    client,
                    "POST",
                    "/runs",
                    stage="create_run",
                    request_id="req_e2e_create_run",
                    expected_statuses={201},
                    json_body={
                        "workflow_type": "episode-research",
                        "payload": {
                            "topic": fixture["topic"],
                            "source_ids": initial_source_ids,
                        },
                    },
                )
                if not isinstance(created, dict) or not isinstance(created.get("id"), str):
                    raise E2EFlowError(stage="create_run", code="run_response_invalid")
                run_id = created["id"]

                waiting = await _poll_for_checkpoint(client, run_id)
                if waiting.get("status") != EXPECTED_WAITING_STATUS:
                    failed_events = await _request_json(
                        client,
                        "GET",
                        f"/runs/{run_id}/events",
                        stage="events_after_terminal_failure",
                        request_id="req_e2e_events_failed",
                        expected_statuses={200},
                    )
                    model_calls = waiting.get("model_calls", [])
                    raise E2EFlowError(
                        stage="poll_checkpoint",
                        code=f"run_stopped_as_{waiting.get('status', 'unknown')}",
                        safe_context={
                            "run": _safe_run_summary(waiting),
                            "events": (
                                _event_summary(failed_events)
                                if isinstance(failed_events, list)
                                else {}
                            ),
                            "usage": {
                                "input_tokens": sum(
                                    int(call.get("input_tokens") or 0) for call in model_calls
                                ),
                                "output_tokens": sum(
                                    int(call.get("output_tokens") or 0) for call in model_calls
                                ),
                                "duration_ms": sum(
                                    int(call.get("duration_ms") or 0) for call in model_calls
                                ),
                                "estimated_costs": _cost_summary(model_calls),
                            },
                        },
                    )

                events_before = await _request_json(
                    client,
                    "GET",
                    f"/runs/{run_id}/events",
                    stage="events_before_resume",
                    request_id="req_e2e_events_before",
                    expected_statuses={200},
                )
                if not isinstance(events_before, list):
                    raise E2EFlowError(
                        stage="events_before_resume",
                        code="events_response_invalid",
                    )

                markdown_before = await _request_markdown(
                    client,
                    f"/runs/{run_id}/exports/interview-scaffold.md",
                    stage="export_waiting",
                    request_id="req_e2e_export_waiting",
                    run_id=run_id,
                )
                paths.markdown.write_text(markdown_before, encoding="utf-8")
                markdown_before_sha = hashlib.sha256(markdown_before.encode("utf-8")).hexdigest()

                supplemental = await _import_source(
                    client,
                    fixture["supplemental_source"],
                    stage="import_supplemental_source",
                    request_id="req_e2e_source_supplemental",
                )
                supplemental_source_id = str(supplemental["source"]["id"])
                resume_payload = {
                    "checkpoint": "interview_scaffold",
                    "submission_id": fixture["submission_id"],
                    "source_ids": [supplemental_source_id],
                }
                resumed = await _request_json(
                    client,
                    "POST",
                    f"/runs/{run_id}/resume",
                    stage="resume",
                    request_id="req_e2e_resume",
                    expected_statuses={200},
                    json_body=resume_payload,
                )
                replay = await _request_json(
                    client,
                    "POST",
                    f"/runs/{run_id}/resume",
                    stage="resume_replay",
                    request_id="req_e2e_resume_replay",
                    expected_statuses={200},
                    json_body=resume_payload,
                )
                if not isinstance(resumed, dict) or not isinstance(replay, dict):
                    raise E2EFlowError(stage="resume", code="resume_response_invalid")

                events_after = await _request_json(
                    client,
                    "GET",
                    f"/runs/{run_id}/events",
                    stage="events_after_resume",
                    request_id="req_e2e_events_after",
                    expected_statuses={200},
                )
                if not isinstance(events_after, list):
                    raise E2EFlowError(
                        stage="events_after_resume",
                        code="events_response_invalid",
                    )
                final_run = await _get_run(
                    client,
                    run_id,
                    stage="get_final_run",
                    request_id="req_e2e_final_run",
                )
                markdown_after = await _request_markdown(
                    client,
                    f"/runs/{run_id}/exports/interview-scaffold.md",
                    stage="export_final",
                    request_id="req_e2e_export_final",
                    run_id=run_id,
                )
    finally:
        application_logger.removeHandler(file_handler)
        file_handler.close()
        for handler in list(application_logger.handlers):
            if handler not in preexisting_handlers and getattr(
                handler, "_epiphany_json_handler", False
            ):
                application_logger.removeHandler(handler)
                handler.close()
        for handler, level in preexisting_handler_levels.items():
            handler.setLevel(level)
        application_logger.setLevel(previous_level)
        application_logger.propagate = previous_propagate

    markdown_after_sha = hashlib.sha256(markdown_after.encode("utf-8")).hexdigest()
    waiting_summary = _safe_run_summary(waiting)
    final_summary = _safe_run_summary(final_run)
    model_calls = final_run.get("model_calls", [])
    submission_artifact = next(
        (
            artifact
            for artifact in final_run.get("artifacts", [])
            if artifact.get("kind") == "user_material_submission"
        ),
        None,
    )
    submission_content = (
        submission_artifact.get("content_json", {}) if isinstance(submission_artifact, dict) else {}
    )
    supplemental_text = fixture["supplemental_source"]["text"]
    expected_model = provider.model
    expected_currency = provider.billing_currency.upper()
    markdown_check_results = _markdown_checks(markdown_before, topic=fixture["topic"])
    events_before_types = [event.get("type") for event in events_before]
    events_after_types = [event.get("type") for event in events_after]
    imported_source_rows = [
        {
            "id": imported["source"]["id"],
            "created": imported["created"],
            "source_type": imported["source"]["source_type"],
            "char_count": imported["source"]["char_count"],
            "segment_count": imported["source"]["segment_count"],
        }
        for imported in [*imported_initial, supplemental]
    ]
    checks = {
        "initial_sources_imported": (
            len(imported_initial) == 3
            and all(imported["source"]["segment_count"] >= 1 for imported in imported_initial)
        ),
        "waiting_checkpoint_reached": (
            waiting.get("workflow_version") == EXPECTED_WORKFLOW_VERSION
            and waiting.get("status") == EXPECTED_WAITING_STATUS
            and waiting.get("current_step") == EXPECTED_WAITING_STEP
        ),
        "waiting_runtime_counts": (
            len(waiting.get("tasks", [])) == 4
            and len(waiting.get("artifacts", [])) == 4
            and len(waiting.get("model_calls", [])) == MAX_MODEL_CALLS
            and waiting.get("model_call_count") == MAX_MODEL_CALLS
        ),
        "waiting_tasks_succeeded": all(
            task.get("status") == "succeeded" for task in waiting.get("tasks", [])
        ),
        "model_calls_succeeded": (
            len(model_calls) == MAX_MODEL_CALLS
            and all(call.get("status") == "succeeded" for call in model_calls)
        ),
        "model_call_identity_matches_provider": (
            provider.name == provider_name
            and len(model_calls) == MAX_MODEL_CALLS
            and all(
                call.get("provider") == provider_name
                and call.get("model") == expected_model
                and str(call.get("cost_currency", "")).upper() == expected_currency
                for call in model_calls
            )
        ),
        "waiting_artifact_kinds_exact": (
            {artifact.get("kind") for artifact in waiting.get("artifacts", [])}
            == EXPECTED_PRE_RESUME_ARTIFACT_KINDS
        ),
        "no_success_before_resume": "run.succeeded" not in events_before_types,
        "waiting_event_present": (
            events_before_types and events_before_types[-1] == "run.waiting_for_user"
        ),
        "markdown_structure_valid": all(markdown_check_results.values()),
        "supplemental_source_imported": supplemental["source"]["segment_count"] >= 1,
        "resume_applied_once": (
            resumed.get("resumed") is True and resumed.get("idempotent_replay") is False
        ),
        "resume_replay_idempotent": (
            replay.get("resumed") is False
            and replay.get("idempotent_replay") is True
            and replay.get("submission_artifact_id") == resumed.get("submission_artifact_id")
        ),
        "final_runtime_counts": (
            len(final_run.get("tasks", [])) == 4
            and len(final_run.get("artifacts", [])) == 5
            and len(final_run.get("model_calls", [])) == MAX_MODEL_CALLS
            and final_run.get("model_call_count") == MAX_MODEL_CALLS
        ),
        "final_status_succeeded": (
            final_run.get("status") == EXPECTED_FINAL_STATUS
            and final_run.get("current_step") == EXPECTED_FINAL_STEP
        ),
        "final_artifact_kinds_exact": (
            {artifact.get("kind") for artifact in final_run.get("artifacts", [])}
            == EXPECTED_FINAL_ARTIFACT_KINDS
        ),
        "submission_references_source_without_copying_text": (
            submission_content.get("source_ids") == [supplemental_source_id]
            and bool(submission_content.get("source_refs"))
            and supplemental_text
            not in json.dumps(
                submission_content,
                ensure_ascii=False,
            )
        ),
        "resume_events_added_once": (
            len(events_after) == len(events_before) + 3
            and events_after_types[-3:]
            == [
                "run.resumed",
                "workflow.user_material.accepted",
                "run.succeeded",
            ]
        ),
        "markdown_stable_after_resume": (
            markdown_before_sha == markdown_after_sha and markdown_before == markdown_after
        ),
        "scaffold_excludes_supplemental_source_text": (
            supplemental_text not in markdown_before and supplemental_text not in markdown_after
        ),
    }

    forbidden_texts = _forbidden_log_fragments(
        fixture,
        secret_values=secret_values,
    )
    log_summary, log_checks_passed = _read_log_summary(
        paths.log,
        forbidden_texts=forbidden_texts,
        provider_name=provider_name,
    )
    checks["logs_structured_and_redacted"] = log_checks_passed
    failures = sorted(name for name, passed in checks.items() if not passed)

    return {
        "event": "checkpoint_e2e.completed",
        "passed": not failures,
        "failures": failures,
        "fixture": {
            "id": fixture["fixture_id"],
            "path": str(paths.fixture),
            "synthetic": True,
            "initial_source_count": len(fixture["initial_sources"]),
            "supplemental_source_count": 1,
        },
        "runtime": {
            "provider": provider_name,
            "model": getattr(provider, "model", "unknown"),
            "database_path": str(paths.database),
            "max_model_calls": MAX_MODEL_CALLS,
            "max_attempts_per_task": MAX_TASK_ATTEMPTS,
            "max_concurrency": MAX_CONCURRENCY,
        },
        "sources": imported_source_rows,
        "waiting_run": waiting_summary,
        "final_run": final_summary,
        "resume": {
            "first_applied": resumed.get("resumed"),
            "first_idempotent_replay": resumed.get("idempotent_replay"),
            "replay_applied": replay.get("resumed"),
            "replay_idempotent": replay.get("idempotent_replay"),
            "submission_artifact_id": resumed.get("submission_artifact_id"),
        },
        "events": {
            "before_resume": _event_summary(events_before),
            "after_resume": _event_summary(events_after),
        },
        "usage": {
            "input_tokens": sum(int(call.get("input_tokens") or 0) for call in model_calls),
            "output_tokens": sum(int(call.get("output_tokens") or 0) for call in model_calls),
            "duration_ms": sum(int(call.get("duration_ms") or 0) for call in model_calls),
            "estimated_costs": _cost_summary(model_calls),
        },
        "markdown": {
            "kind": "interview_scaffold",
            "path": str(paths.markdown),
            "char_count": len(markdown_before),
            "sha256": markdown_before_sha,
            "structure": markdown_check_results,
            "stable_after_resume": markdown_before_sha == markdown_after_sha,
            "not_yet_final_podcast_draft": True,
        },
        "logs": log_summary,
        "checks": checks,
    }


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _print_json(payload: dict[str, Any], *, stream: Any | None = None) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, indent=2),
        file=stream if stream is not None else sys.stdout,
        flush=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise the complete M3.1 Source -> Run -> Markdown -> Resume API journey. "
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
        help="synthetic JSON fixture",
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
        help="ignored directory for logs, report, and Markdown",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = E2EPaths(
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
        fixture = load_fixture(paths.fixture)
    except E2EFlowError as error:
        _print_json(
            {
                "event": "checkpoint_e2e.blocked",
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
                "event": "checkpoint_e2e.blocked",
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
        safe_context = getattr(error, "safe_context", {})
        log_summary: dict[str, Any] | None = None
        if paths.log.exists() and getattr(error, "stage", None) not in {
            "database_preflight",
            "provider",
        }:
            forbidden_texts = _forbidden_log_fragments(
                fixture,
                secret_values=(api_key,),
            )
            try:
                log_summary, _ = _read_log_summary(
                    paths.log,
                    forbidden_texts=forbidden_texts,
                    provider_name=args.provider,
                )
            except E2EFlowError:
                log_summary = {
                    "path": str(paths.log),
                    "validation": "failed",
                }
        failure_report = {
            "event": "checkpoint_e2e.crashed",
            "passed": False,
            "stage": getattr(error, "stage", "unexpected"),
            "error_code": getattr(error, "code", type(error).__name__),
            "message": "Inspect the sanitized report, runtime log, and dedicated database.",
            "paths": {
                "fixture": str(paths.fixture),
                "database": str(paths.database),
                "log": str(paths.log),
                "report": str(paths.report),
                "markdown": str(paths.markdown),
            },
            "evidence": safe_context,
            "logs": log_summary,
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
            "markdown_path": report["markdown"]["path"],
            "markdown_sha256": report["markdown"]["sha256"],
        }
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
