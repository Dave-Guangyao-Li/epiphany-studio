from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic.config import Config

from alembic import command
from epiphany.config import Settings
from epiphany.db import Database
from epiphany.observability import configure_logging
from epiphany.runtime.orchestrator import Orchestrator
from epiphany.runtime.providers import DeepSeekProvider, ModelProvider
from epiphany.runtime.worker import Worker
from epiphany.schemas import RunView
from epiphany.services import RunService
from epiphany.source_service import SourceService

LIVE_MODEL = "deepseek-v4-flash"
MAX_MODEL_CALLS = 3
EXPECTED_RESEARCH_CHILDREN = 2
MAX_TASK_ATTEMPTS = 1
MAX_CONCURRENCY = 1
MAX_OUTPUT_TOKENS_PER_CALL = 800
MAX_SOURCE_CHARS = 4_000
WORKER_TIMEOUT_SECONDS = 90
HTTP_TIMEOUT_SECONDS = WORKER_TIMEOUT_SECONDS + 5

SYNTHETIC_SOURCE = """2019年，我第一次开始用日记记录一个长期项目。

2021年，我在出发去陌生城市生活前录下了一段语音，既期待，也有一点紧张。

2026年，我重新听见那段录音，发现声音像一个时间胶囊，让不同年份的自己重新相遇。"""

EXPECTED_ARTIFACT_KINDS = {
    "timeline_research_result",
    "theme_research_result",
    "episode_research_bundle",
    "build_interview_scaffold_result",
}


def build_preflight(
    *,
    execute: bool,
    api_key_present: bool,
    database_path: str,
    billing_currency: str = "USD",
) -> dict[str, Any]:
    """Return a safe-to-print plan without ever accepting the API key value."""

    return {
        "event": "live_smoke.preflight",
        "mode": "execute" if execute else "dry-run",
        "execute_requested": execute,
        "network_enabled": execute and api_key_present,
        "paid_api_call_possible": execute and api_key_present,
        "synthetic_source_only": True,
        "provider": "deepseek",
        "model": LIVE_MODEL,
        "max_model_calls_per_run": MAX_MODEL_CALLS,
        "max_attempts_per_task": MAX_TASK_ATTEMPTS,
        "max_concurrency": MAX_CONCURRENCY,
        "max_output_tokens_per_call": MAX_OUTPUT_TOKENS_PER_CALL,
        "worker_timeout_seconds": WORKER_TIMEOUT_SECONDS,
        "api_key_status": "present" if api_key_present else "absent",
        "database_path": database_path,
        "billing_currency": billing_currency,
        "expected_cost": {
            "currency": billing_currency,
            "upper_bound": "0.01",
            "is_estimate": True,
        },
    }


async def run_smoke_workflow(
    *,
    database_url: str,
    provider: ModelProvider,
) -> tuple[RunView, int]:
    """Run two Researchers and one sequential Interviewer against a migrated database."""

    database = Database(database_url)
    orchestrator = Orchestrator(task_max_attempts=MAX_TASK_ATTEMPTS)
    service = RunService(database, orchestrator)
    worker = Worker(
        database=database,
        orchestrator=orchestrator,
        provider=provider,
        lease_seconds=120,
        timeout_seconds=WORKER_TIMEOUT_SECONDS,
        poll_interval_seconds=0.01,
        # The normal Runtime already proves parallel fan-out with Fake/Mock
        # Providers. The paid smoke is serial so a first-child failure can
        # cancel its queued sibling before a second request is sent.
        max_concurrency=MAX_CONCURRENCY,
        max_model_calls_per_run=MAX_MODEL_CALLS,
    )

    try:
        imported = await SourceService(database).import_text(
            title="DeepSeek live smoke synthetic source",
            source_type="podcast_draft",
            text=SYNTHETIC_SOURCE,
            metadata={"fixture": "m2.3b-live-smoke", "contains_personal_data": False},
        )
        created = await service.create_run(
            workflow_type="episode-research",
            payload={
                "topic": "声音如何让不同年份的自己重新相遇",
                "source_ids": [imported.source.id],
            },
        )
        child_tasks = [task for task in created.tasks if task.parent_task_id is not None]
        safety_boundary_ready = (
            len(child_tasks) == EXPECTED_RESEARCH_CHILDREN
            and all(task.max_attempts == MAX_TASK_ATTEMPTS for task in child_tasks)
            and worker.max_model_calls_per_run == MAX_MODEL_CALLS
            and worker.max_concurrency == MAX_CONCURRENCY
        )
        if not safety_boundary_ready:
            raise RuntimeError("live smoke safety boundary was not installed")

        processed_tasks = await worker.run_until_idle(max_tasks=MAX_MODEL_CALLS)
        completed = await service.get_run(created.id)
        return completed, processed_tasks
    finally:
        await database.close()


def build_sanitized_summary(
    run: RunView,
    *,
    processed_tasks: int,
    database_path: str,
) -> dict[str, Any]:
    """Expose trace metadata only; never expose source or generated content."""

    artifact_kinds = sorted(artifact.kind for artifact in run.artifacts)
    call_rows = [
        {
            "id": call.id,
            "task_id": call.task_id,
            "attempt": call.attempt,
            "provider": call.provider,
            "model": call.model,
            "status": call.status,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "duration_ms": call.duration_ms,
            "estimated_cost_micros": call.estimated_cost_micros,
            "cost_currency": call.cost_currency,
            "error_code": call.error_code,
        }
        for call in run.model_calls
    ]
    cost_micros_by_currency: dict[str, int] = {}
    for call in run.model_calls:
        currency = call.cost_currency.upper()
        cost_micros_by_currency[currency] = (
            cost_micros_by_currency.get(currency, 0) + call.estimated_cost_micros
        )
    estimated_costs = {
        currency: {
            "micros": micros,
            "amount": f"{Decimal(micros) / Decimal(1_000_000):.6f}",
        }
        for currency, micros in sorted(cost_micros_by_currency.items())
    }
    passed = (
        run.status == "succeeded"
        and processed_tasks == MAX_MODEL_CALLS
        and run.model_call_count == MAX_MODEL_CALLS
        and len(run.model_calls) == MAX_MODEL_CALLS
        and all(call.status == "succeeded" for call in run.model_calls)
        and set(artifact_kinds) == EXPECTED_ARTIFACT_KINDS
    )

    return {
        "event": "live_smoke.completed",
        "passed": passed,
        "database_path": database_path,
        "run": {
            "id": run.id,
            "status": run.status,
            "current_step": run.current_step,
            "model_call_count": run.model_call_count,
        },
        "processed_tasks": processed_tasks,
        "tasks": [
            {
                "id": task.id,
                "parent_task_id": task.parent_task_id,
                "kind": task.kind,
                "status": task.status,
                "attempt": task.attempt,
                "max_attempts": task.max_attempts,
                "error_code": task.error_code,
            }
            for task in run.tasks
        ],
        "model_calls": call_rows,
        "totals": {
            "input_tokens": sum(call.input_tokens for call in run.model_calls),
            "output_tokens": sum(call.output_tokens for call in run.model_calls),
            "estimated_costs": estimated_costs,
        },
        "artifact_kinds": artifact_kinds,
    }


def database_url_for_path(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.expanduser().resolve()}"


@contextmanager
def _temporary_database_url(database_url: str) -> Any:
    previous = os.environ.get("EPIPHANY_DATABASE_URL")
    os.environ["EPIPHANY_DATABASE_URL"] = database_url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("EPIPHANY_DATABASE_URL", None)
        else:
            os.environ["EPIPHANY_DATABASE_URL"] = previous


def migrate_database(database_url: str) -> None:
    """Apply the production Alembic migrations to the dedicated smoke database."""

    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("prepend_sys_path", str(backend_dir / "src"))
    config.set_main_option("sqlalchemy.url", database_url)
    with _temporary_database_url(database_url):
        command.upgrade(config, "head")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a bounded three-call DeepSeek smoke test with synthetic source material. "
            "Without --execute this command is a zero-network dry run."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="allow the three external DeepSeek API calls",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/deepseek-live-smoke.db"),
        help="dedicated SQLite trace database (default: data/deepseek-live-smoke.db)",
    )
    return parser


def _print_json(payload: dict[str, Any], *, stream: Any | None = None) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, indent=2),
        file=stream if stream is not None else sys.stdout,
        flush=True,
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
    api_key_present = bool(api_key)
    database_display_path = str(args.database)

    _print_json(
        build_preflight(
            execute=args.execute,
            api_key_present=api_key_present,
            database_path=database_display_path,
            billing_currency=settings.deepseek_billing_currency,
        )
    )
    if not args.execute:
        return 0
    if not api_key:
        _print_json(
            {
                "event": "live_smoke.blocked",
                "error_code": "deepseek_api_key_missing",
                "message": (
                    "Add EPIPHANY_DEEPSEEK_API_KEY to backend/.env, then rerun with --execute."
                ),
            },
            stream=sys.stderr,
        )
        return 2

    database_url = database_url_for_path(args.database)
    migrate_database(database_url)
    configure_logging(settings.log_level)
    provider = DeepSeekProvider(
        api_key=api_key,
        model=LIVE_MODEL,
        billing_currency=settings.deepseek_billing_currency,
        base_url="https://api.deepseek.com",
        max_tokens=MAX_OUTPUT_TOKENS_PER_CALL,
        max_source_chars=MAX_SOURCE_CHARS,
        request_timeout_seconds=HTTP_TIMEOUT_SECONDS,
    )

    try:
        completed, processed_tasks = asyncio.run(
            run_smoke_workflow(
                database_url=database_url,
                provider=provider,
            )
        )
    except Exception as error:
        _print_json(
            {
                "event": "live_smoke.crashed",
                "error_type": type(error).__name__,
                "error_code": getattr(error, "code", "live_smoke_error"),
                "message": "The smoke test stopped unexpectedly; inspect the structured logs.",
            },
            stream=sys.stderr,
        )
        return 1

    summary = build_sanitized_summary(
        completed,
        processed_tasks=processed_tasks,
        database_path=database_display_path,
    )
    _print_json(summary)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
