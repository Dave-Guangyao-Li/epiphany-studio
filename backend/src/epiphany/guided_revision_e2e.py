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
from typing import Any

import httpx
from pydantic import ValidationError

from epiphany.checkpoint_e2e import (
    BACKEND_DIR,
    E2EFlowError,
    _assert_database_has_no_active_tasks,
    _event_summary,
    _forbidden_log_fragments,
    _get_run,
    _import_source,
    _poll_for_checkpoint,
    _poll_for_terminal,
    _request_json,
    _request_markdown,
    _safe_run_summary,
    _write_report,
)
from epiphany.config import Settings
from epiphany.draft_feedback_schemas import DraftUserFeedbackRequest
from epiphany.editor_schemas import editor_output_reference_keys
from epiphany.live_deepseek_smoke import database_url_for_path, migrate_database
from epiphany.main import create_app
from epiphany.observability import JsonFormatter, RequestContextFilter
from epiphany.quality_contract_e2e import load_quality_contract_fixture
from epiphany.quality_contract_schemas import CreativeBrief
from epiphany.runtime.orchestrator import GUIDED_REVISION_WORKFLOW_VERSION
from epiphany.runtime.providers import FakeProvider
from epiphany.schemas import CreateSourceRequest

DEFAULT_FIXTURE_PATH = BACKEND_DIR / "fixtures/e2e/m3-6-guided-revision.zh-CN.json"
DEFAULT_DATABASE_PATH = BACKEND_DIR / "data/guided-revision-e2e.db"
DEFAULT_OUTPUT_DIR = BACKEND_DIR / "artifacts/guided-revision-e2e"
STYLE_MARKER = "风格样本里的蓝色旧雨伞只用于验证个人表达通道"


@dataclass(frozen=True)
class GuidedRevisionPaths:
    fixture: Path
    database: Path
    output_dir: Path

    @property
    def log(self) -> Path:
        return self.output_dir / "runtime.jsonl"

    @property
    def report(self) -> Path:
        return self.output_dir / "report.json"

    def markdown(self, candidate: str, kind: str) -> Path:
        return self.output_dir / f"{candidate}-{kind}.md"

    def json_artifact(self, name: str) -> Path:
        return self.output_dir / f"{name}.json"


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise E2EFlowError(stage="fixture", code="fixture_unreadable") from error
    if not isinstance(value, dict):
        raise E2EFlowError(stage="fixture", code="fixture_root_invalid")
    return value


def load_guided_revision_fixture(path: Path) -> dict[str, Any]:
    """Load a small M3.6 overlay on the existing realistic M3.3 material."""

    overlay = _load_json(path)
    source_fixture_name = overlay.get("source_fixture")
    if not isinstance(source_fixture_name, str) or not source_fixture_name.strip():
        raise E2EFlowError(stage="fixture", code="source_fixture_invalid")
    source_fixture = (path.parent / source_fixture_name).resolve()
    if source_fixture.parent != (BACKEND_DIR / "fixtures/e2e").resolve():
        raise E2EFlowError(stage="fixture", code="source_fixture_outside_fixture_dir")
    base = load_quality_contract_fixture(source_fixture)

    try:
        creative_brief = CreativeBrief.model_validate(overlay.get("creative_brief"))
        writing_sample = CreateSourceRequest.model_validate(overlay.get("writing_sample"))
        feedback = DraftUserFeedbackRequest.model_validate(overlay.get("synthetic_feedback"))
    except (ValidationError, TypeError) as error:
        raise E2EFlowError(stage="fixture", code="fixture_contract_invalid") from error
    style_reference = overlay.get("style_reference")
    revision = overlay.get("revision")
    if not isinstance(style_reference, dict) or not isinstance(revision, dict):
        raise E2EFlowError(stage="fixture", code="fixture_revision_contract_invalid")
    if writing_sample.source_type != "writing_sample":
        raise E2EFlowError(stage="fixture", code="writing_sample_type_invalid")
    if (
        writing_sample.metadata.get("synthetic") is not True
        or writing_sample.metadata.get("contains_personal_data") is not False
        or feedback.feedback_origin != "synthetic_test"
    ):
        raise E2EFlowError(stage="fixture", code="fixture_privacy_marker_invalid")
    if STYLE_MARKER not in writing_sample.text:
        raise E2EFlowError(stage="fixture", code="style_marker_missing")
    supplemental_extension = overlay.get("supplemental_extension")
    if not isinstance(supplemental_extension, str) or len(supplemental_extension.strip()) < 800:
        raise E2EFlowError(stage="fixture", code="supplemental_extension_invalid")
    required_revision_fields = ("submission_id", "selected_actions", "revision_instruction")
    if any(not revision.get(field) for field in required_revision_fields):
        raise E2EFlowError(stage="fixture", code="fixture_revision_contract_invalid")

    supplemental_source = dict(base["supplemental_source"])
    supplemental_source["text"] = (
        f"{supplemental_source['text'].rstrip()}\n\n{supplemental_extension.strip()}"
    )
    return {
        **base,
        "fixture_id": overlay["fixture_id"],
        "source_fixture_path": str(source_fixture),
        "creative_brief": creative_brief.model_dump(mode="json"),
        "supplemental_source": supplemental_source,
        "writing_sample": writing_sample.model_dump(mode="json"),
        "style_reference": dict(style_reference),
        "synthetic_feedback": feedback.model_dump(mode="json"),
        "revision": dict(revision),
    }


def build_preflight(
    *, execute: bool, fixture: dict[str, Any], paths: GuidedRevisionPaths
) -> dict[str, Any]:
    return {
        "event": "guided_revision_e2e.preflight",
        "mode": "execute" if execute else "dry-run",
        "provider": "fake",
        "network_enabled": False,
        "paid_api_call_possible": False,
        "synthetic_source_only": True,
        "fixture_id": fixture["fixture_id"],
        "parent_expected_model_calls": 5,
        "child_expected_model_calls": 2,
        "human_checkpoint_count": 1,
        "feedback_origin": "synthetic_test",
        "paths": {
            "fixture": str(paths.fixture),
            "database": str(paths.database),
            "output_dir": str(paths.output_dir),
            "log": str(paths.log),
            "report": str(paths.report),
        },
    }


async def _events(
    client: httpx.AsyncClient,
    run_id: str,
    request_id: str,
) -> list[dict[str, Any]]:
    value = await _request_json(
        client,
        "GET",
        f"/runs/{run_id}/events",
        stage="events",
        request_id=request_id,
        expected_statuses={200},
    )
    if not isinstance(value, list):
        raise E2EFlowError(stage="events", code="events_response_invalid")
    return value


async def _markdown(
    client: httpx.AsyncClient,
    *,
    run_id: str,
    kind: str,
    request_id: str,
) -> str:
    if kind in {"podcast-draft", "show-notes", "interview-scaffold"}:
        return await _request_markdown(
            client,
            f"/runs/{run_id}/exports/{kind}.md",
            stage=f"export_{kind}",
            request_id=request_id,
            run_id=run_id,
            filename_prefix=kind,
        )
    response = await client.get(
        f"/runs/{run_id}/exports/quality-report.md",
        headers={"X-Request-ID": request_id},
    )
    if response.status_code != 200:
        raise E2EFlowError(stage="export_quality", code="quality_markdown_unavailable")
    if response.headers.get("X-Request-ID") != request_id:
        raise E2EFlowError(stage="export_quality", code="response_request_id_mismatch")
    return response.text


def _artifact(run: dict[str, Any], *, artifact_id: str | None = None, kind: str | None = None):
    matches = [
        item
        for item in run.get("artifacts", [])
        if (artifact_id is None or item.get("id") == artifact_id)
        and (kind is None or item.get("kind") == kind)
    ]
    if len(matches) != 1:
        raise E2EFlowError(
            stage="artifact_validation",
            code="artifact_not_unique",
            safe_context={"kind": kind, "artifact_id": artifact_id, "count": len(matches)},
        )
    return matches[0]


def _draft_reference_keys(artifact: dict[str, Any]) -> set[tuple[str, str]]:
    content = dict(artifact["content_json"])
    content.pop("_execution", None)
    return set(editor_output_reference_keys(content))


def _log_summary(
    path: Path,
    *,
    forbidden_fragments: Sequence[str],
) -> tuple[dict[str, Any], bool]:
    raw = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
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
    counts = Counter(str(row["event"]) for row in rows if row.get("event"))
    required = {
        "run.waiting_for_user",
        "run.resume.accepted",
        "workflow.draft_improvement.planned",
        "workflow.draft_quality.feedback_recorded",
        "workflow.draft_revision.requested",
        "workflow.draft_revision.queued",
        "workflow.draft_revision.compared",
    }
    redacted = all(fragment not in raw for fragment in forbidden_fragments if fragment)
    structured = bool(rows) and required.issubset(counts)
    return (
        {
            "path": str(path),
            "line_count": len(rows),
            "event_counts": dict(sorted(counts.items())),
            "required_events_present": required.issubset(counts),
            "source_style_feedback_text_absent": redacted,
        },
        structured and redacted,
    )


async def execute_fake_e2e(
    *,
    fixture: dict[str, Any],
    paths: GuidedRevisionPaths,
) -> dict[str, Any]:
    paths.database.parent.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    database_url = database_url_for_path(paths.database)
    await asyncio.to_thread(migrate_database, database_url)
    await asyncio.to_thread(_assert_database_has_no_active_tasks, paths.database)
    settings = Settings(
        database_url=database_url,
        create_schema_on_start=False,
        log_level="INFO",
        worker_enabled=True,
        worker_poll_interval_seconds=0.02,
        worker_max_concurrency=1,
        worker_lease_seconds=150,
        task_timeout_seconds=120,
        task_max_attempts=1,
        model_max_calls_per_run=5,
        model_provider="fake",
    )

    handler = logging.FileHandler(paths.log, mode="w", encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())
    logger = logging.getLogger("epiphany")
    old_handlers = set(logger.handlers)
    old_level = logger.level
    old_propagate = logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        app = create_app(settings=settings, provider=FakeProvider())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://epiphany-guided-revision-e2e",
                timeout=30,
            ) as client:
                initial = [
                    await _import_source(
                        client,
                        source,
                        stage=f"import_initial_{index}",
                        request_id=f"req_m36_initial_{index}",
                    )
                    for index, source in enumerate(fixture["initial_sources"], start=1)
                ]
                style = await _import_source(
                    client,
                    fixture["writing_sample"],
                    stage="import_style",
                    request_id="req_m36_style",
                )
                factual_source_ids = [str(item["source"]["id"]) for item in initial]
                style_source_id = str(style["source"]["id"])
                style_contract = fixture["style_reference"]
                created = await _request_json(
                    client,
                    "POST",
                    "/runs",
                    stage="create_parent",
                    request_id="req_m36_parent",
                    expected_statuses={201},
                    json_body={
                        "workflow_type": "episode-research",
                        "payload": {
                            "topic": fixture["topic"],
                            "source_ids": factual_source_ids,
                            "creative_brief": fixture["creative_brief"],
                            "draft_quality": {"enabled": True},
                            "writing_style_reference": {
                                "samples": [
                                    {
                                        "source_id": style_source_id,
                                        "sample_kind": style_contract["sample_kind"],
                                    }
                                ],
                                "ownership_attested": style_contract["ownership_attested"],
                                "model_processing_consent": style_contract[
                                    "model_processing_consent"
                                ],
                                "usage": style_contract["usage"],
                            },
                        },
                    },
                )
                if not isinstance(created, dict):
                    raise E2EFlowError(stage="create_parent", code="run_response_invalid")
                parent_id = str(created["id"])
                waiting = await _poll_for_checkpoint(client, parent_id)
                scaffold = await _markdown(
                    client,
                    run_id=parent_id,
                    kind="interview-scaffold",
                    request_id="req_m36_scaffold",
                )
                supplemental = await _import_source(
                    client,
                    fixture["supplemental_source"],
                    stage="import_supplement",
                    request_id="req_m36_supplement",
                )
                supplemental_id = str(supplemental["source"]["id"])
                resume_body = {
                    "checkpoint": "material_readiness",
                    "submission_id": fixture["submission_id"],
                    "source_ids": [supplemental_id],
                }
                resumed = await _request_json(
                    client,
                    "POST",
                    f"/runs/{parent_id}/resume",
                    stage="resume",
                    request_id="req_m36_resume",
                    expected_statuses={200},
                    json_body=resume_body,
                )
                resume_replay = await _request_json(
                    client,
                    "POST",
                    f"/runs/{parent_id}/resume",
                    stage="resume_replay",
                    request_id="req_m36_resume_replay",
                    expected_statuses={200},
                    json_body=resume_body,
                )
                parent = await _poll_for_terminal(client, parent_id)
                parent_draft = _artifact(parent, artifact_id=str(parent["output_artifact_id"]))
                parent_quality = _artifact(parent, kind="draft_quality_report")
                style_profile = _artifact(parent, kind="writing_style_profile")
                parent_snapshot = {
                    "draft_sha256": _canonical_sha256(parent_draft["content_json"]),
                    "quality_sha256": _canonical_sha256(parent_quality["content_json"]),
                    "output_artifact_id": parent["output_artifact_id"],
                    "task_count": len(parent["tasks"]),
                    "model_call_count": len(parent["model_calls"]),
                }

                plan = await _request_json(
                    client,
                    "GET",
                    f"/runs/{parent_id}/improvement-plan",
                    stage="improvement_plan",
                    request_id="req_m36_plan",
                    expected_statuses={200},
                )
                plan_replay = await _request_json(
                    client,
                    "GET",
                    f"/runs/{parent_id}/improvement-plan",
                    stage="improvement_plan_replay",
                    request_id="req_m36_plan_replay",
                    expected_statuses={200},
                )
                feedback = await _request_json(
                    client,
                    "POST",
                    f"/runs/{parent_id}/quality-feedback",
                    stage="feedback",
                    request_id="req_m36_feedback",
                    expected_statuses={200},
                    json_body=fixture["synthetic_feedback"],
                )
                feedback_replay = await _request_json(
                    client,
                    "POST",
                    f"/runs/{parent_id}/quality-feedback",
                    stage="feedback_replay",
                    request_id="req_m36_feedback_replay",
                    expected_statuses={200},
                    json_body=fixture["synthetic_feedback"],
                )
                if not all(
                    isinstance(item, dict)
                    for item in (plan, plan_replay, feedback, feedback_replay)
                ):
                    raise E2EFlowError(stage="revision_input", code="response_invalid")
                lower_option = next(
                    (
                        option
                        for option in plan["plan"]["options"]
                        if option["kind"] == "lower_target_duration"
                    ),
                    None,
                )
                if lower_option is None:
                    raise E2EFlowError(stage="revision_input", code="lower_target_unavailable")
                revision_body = {
                    "submission_id": fixture["revision"]["submission_id"],
                    "selected_actions": fixture["revision"]["selected_actions"],
                    "selected_feedback_artifact_ids": [feedback["artifact"]["id"]],
                    "target_duration_minutes": lower_option["suggested_target_duration_minutes"],
                    "revision_instruction": fixture["revision"]["revision_instruction"],
                }
                revision_created = await _request_json(
                    client,
                    "POST",
                    f"/runs/{parent_id}/revisions",
                    stage="create_revision",
                    request_id="req_m36_revision",
                    expected_statuses={201},
                    json_body=revision_body,
                )
                revision_replay = await _request_json(
                    client,
                    "POST",
                    f"/runs/{parent_id}/revisions",
                    stage="revision_replay",
                    request_id="req_m36_revision_replay",
                    expected_statuses={200},
                    json_body=revision_body,
                )
                if not isinstance(revision_created, dict) or not isinstance(revision_replay, dict):
                    raise E2EFlowError(stage="create_revision", code="response_invalid")
                child_id = str(revision_created["run"]["id"])
                child = await _poll_for_terminal(client, child_id)
                child_draft = _artifact(child, artifact_id=str(child["output_artifact_id"]))
                child_quality = await _request_json(
                    client,
                    "GET",
                    f"/runs/{child_id}/quality-report",
                    stage="child_quality",
                    request_id="req_m36_child_quality",
                    expected_statuses={200},
                )
                comparison = await _request_json(
                    client,
                    "GET",
                    f"/runs/{child_id}/revision-comparison",
                    stage="comparison",
                    request_id="req_m36_comparison",
                    expected_statuses={200},
                )
                comparison_replay = await _request_json(
                    client,
                    "GET",
                    f"/runs/{child_id}/revision-comparison",
                    stage="comparison_replay",
                    request_id="req_m36_comparison_replay",
                    expected_statuses={200},
                )
                parent_after = await _get_run(
                    client,
                    parent_id,
                    stage="parent_after",
                    request_id="req_m36_parent_after",
                )
                parent_events = await _events(client, parent_id, "req_m36_parent_events")
                child_events = await _events(client, child_id, "req_m36_child_events")

                markdown = {
                    "parent-podcast-draft": await _markdown(
                        client,
                        run_id=parent_id,
                        kind="podcast-draft",
                        request_id="req_m36_parent_draft",
                    ),
                    "parent-show-notes": await _markdown(
                        client,
                        run_id=parent_id,
                        kind="show-notes",
                        request_id="req_m36_parent_notes",
                    ),
                    "parent-quality-report": await _markdown(
                        client,
                        run_id=parent_id,
                        kind="quality-report",
                        request_id="req_m36_parent_quality",
                    ),
                    "child-podcast-draft": await _markdown(
                        client,
                        run_id=child_id,
                        kind="podcast-draft",
                        request_id="req_m36_child_draft",
                    ),
                    "child-show-notes": await _markdown(
                        client,
                        run_id=child_id,
                        kind="show-notes",
                        request_id="req_m36_child_notes",
                    ),
                    "child-quality-report": await _markdown(
                        client,
                        run_id=child_id,
                        kind="quality-report",
                        request_id="req_m36_child_quality_md",
                    ),
                }
    finally:
        logger.removeHandler(handler)
        handler.close()
        for extra in list(logger.handlers):
            if extra not in old_handlers and getattr(extra, "_epiphany_json_handler", False):
                logger.removeHandler(extra)
                extra.close()
        logger.setLevel(old_level)
        logger.propagate = old_propagate

    for name, content in markdown.items():
        candidate, kind = name.split("-", maxsplit=1)
        paths.markdown(candidate, kind).write_text(content, encoding="utf-8")
    paths.markdown("parent", "interview-scaffold").write_text(scaffold, encoding="utf-8")
    paths.json_artifact("improvement-plan").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    paths.json_artifact("revision-comparison").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    persisted_parent_draft = _artifact(
        parent_after, artifact_id=parent_snapshot["output_artifact_id"]
    )
    persisted_parent_quality = _artifact(parent_after, kind="draft_quality_report")
    parent_refs = _draft_reference_keys(persisted_parent_draft)
    child_refs = _draft_reference_keys(child_draft)
    profile_text = json.dumps(style_profile["content_json"], ensure_ascii=False)
    feedback_comment = str(fixture["synthetic_feedback"].get("comment", ""))
    forbidden = [
        *_forbidden_log_fragments(fixture),
        fixture["writing_sample"]["text"],
        STYLE_MARKER,
        feedback_comment,
    ]
    log_summary, logs_valid = _log_summary(paths.log, forbidden_fragments=forbidden)
    checks = {
        "parent_waited_for_material": (
            waiting.get("status") == "waiting_for_user"
            and waiting.get("current_step") == "awaiting_more_material"
        ),
        "resume_idempotent": (
            isinstance(resumed, dict)
            and resumed.get("resumed") is True
            and isinstance(resume_replay, dict)
            and resume_replay.get("idempotent_replay") is True
        ),
        "parent_succeeded_with_five_calls": (
            parent.get("status") == "succeeded" and len(parent.get("model_calls", [])) == 5
        ),
        "style_profile_ready_and_text_free": (
            style_profile["content_json"]["readiness"]["status"] == "ready"
            and '"text"' not in profile_text
            and STYLE_MARKER not in profile_text
        ),
        "style_source_never_factual_citation": (
            all(source_id != style_source_id for source_id, _ in parent_refs | child_refs)
        ),
        "plan_idempotent": plan["artifact"]["id"] == plan_replay["artifact"]["id"],
        "synthetic_feedback_idempotent_and_ineligible": (
            feedback["feedback"]["feedback_origin"] == "synthetic_test"
            and feedback["feedback"]["human_signal_eligible"] is False
            and feedback_replay["idempotent_replay"] is True
        ),
        "revision_request_idempotent": (
            revision_created["idempotent_replay"] is False
            and revision_replay["idempotent_replay"] is True
            and revision_created["run"]["id"] == revision_replay["run"]["id"]
        ),
        "child_independent_two_call_run": (
            child.get("status") == "succeeded"
            and child.get("parent_run_id") == parent_id
            and len(child.get("model_calls", [])) == 2
            and {task["kind"] for task in child.get("tasks", [])}
            == {"revise_podcast_draft", "review_podcast_draft"}
        ),
        "parent_draft_and_report_immutable": (
            _canonical_sha256(persisted_parent_draft["content_json"])
            == parent_snapshot["draft_sha256"]
            and _canonical_sha256(persisted_parent_quality["content_json"])
            == parent_snapshot["quality_sha256"]
            and parent_after["output_artifact_id"] == parent_snapshot["output_artifact_id"]
            and len(parent_after["tasks"]) == parent_snapshot["task_count"]
            and len(parent_after["model_calls"]) == parent_snapshot["model_call_count"]
        ),
        "child_quality_is_style_aware": (
            isinstance(child_quality, dict)
            and child_quality["report"]["writing_style_context_status"] == "ready"
            and child_quality["report"]["requires_human_review"] is True
        ),
        "comparison_idempotent_and_no_auto_winner": (
            isinstance(comparison, dict)
            and isinstance(comparison_replay, dict)
            and comparison["artifact"]["id"] == comparison_replay["artifact"]["id"]
            and comparison["comparison"]["automatic_winner_selected"] is False
            and comparison["comparison"]["requires_human_review"] is True
        ),
        "markdown_exports_written": all(content.startswith("# ") for content in markdown.values()),
        "runtime_logs_structured_and_redacted": logs_valid,
    }
    failures = sorted(name for name, passed in checks.items() if not passed)
    return {
        "event": "guided_revision_e2e.completed",
        "passed": not failures,
        "failures": failures,
        "fixture": {
            "id": fixture["fixture_id"],
            "path": str(paths.fixture),
            "source_fixture_path": fixture["source_fixture_path"],
            "synthetic": True,
        },
        "runtime": {
            "provider": "fake",
            "network_enabled": False,
            "database_path": str(paths.database),
        },
        "parent": _safe_run_summary(parent_after),
        "child": _safe_run_summary(child),
        "events": {
            "parent": _event_summary(parent_events),
            "child": _event_summary(child_events),
        },
        "workflow": {
            "workflow_version": GUIDED_REVISION_WORKFLOW_VERSION,
            "style_source_id": style_source_id,
            "supplemental_source_id": supplemental_id,
            "plan_artifact_id": plan["artifact"]["id"],
            "feedback_artifact_id": feedback["artifact"]["id"],
            "revision_request_artifact_id": revision_created["request_artifact_id"],
            "comparison_artifact_id": comparison["artifact"]["id"],
        },
        "outputs": {
            "improvement_plan": str(paths.json_artifact("improvement-plan")),
            "revision_comparison": str(paths.json_artifact("revision-comparison")),
            "markdown": {
                name: str(paths.markdown(*name.split("-", maxsplit=1))) for name in markdown
            },
            "interview_scaffold": str(paths.markdown("parent", "interview-scaffold")),
        },
        "logs": log_summary,
        "checks": checks,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise the zero-cost M3.6 parent checkpoint, deterministic Plan, "
            "synthetic feedback, explicit child revision, Reviewer, comparison, "
            "and Markdown exports. Without --execute only a safe preflight is printed."
        )
    )
    parser.add_argument("--execute", action="store_true")
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
    paths = GuidedRevisionPaths(
        fixture=args.fixture.expanduser().resolve(),
        database=args.database.expanduser().resolve(),
        output_dir=args.output_dir.expanduser().resolve(),
    )
    try:
        fixture = load_guided_revision_fixture(paths.fixture)
    except E2EFlowError as error:
        _print(
            {
                "event": "guided_revision_e2e.blocked",
                "stage": error.stage,
                "error_code": error.code,
            },
            stream=sys.stderr,
        )
        return 2
    _print(build_preflight(execute=args.execute, fixture=fixture, paths=paths))
    if not args.execute:
        return 0
    try:
        report = asyncio.run(execute_fake_e2e(fixture=fixture, paths=paths))
    except Exception as error:
        paths.output_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "event": "guided_revision_e2e.crashed",
            "passed": False,
            "stage": getattr(error, "stage", "unexpected"),
            "error_code": getattr(error, "code", type(error).__name__),
            "evidence": getattr(error, "safe_context", {}),
            "paths": {
                "database": str(paths.database),
                "log": str(paths.log),
                "report": str(paths.report),
            },
        }
        _write_report(paths.report, report)
        _print(report, stream=sys.stderr)
        return 1
    _write_report(paths.report, report)
    _print(
        {
            "event": report["event"],
            "passed": report["passed"],
            "failures": report["failures"],
            "parent_run_id": report["parent"]["id"],
            "child_run_id": report["child"]["id"],
            "report_path": str(paths.report),
            "log_path": str(paths.log),
        }
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
