from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from epiphany.db import Database
from epiphany.live_deepseek_smoke import (
    MAX_MODEL_CALLS,
    SYNTHETIC_SOURCE,
    build_preflight,
    build_sanitized_summary,
    database_url_for_path,
    main,
    migrate_database,
    run_smoke_workflow,
)
from epiphany.runtime.providers import (
    FakeProvider,
    ProviderInvalidRequestError,
    ProviderResult,
    TaskInvocation,
)


class FirstCallFailureProvider(FakeProvider):
    def __init__(self) -> None:
        self.invocations = 0

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        self.invocations += 1
        raise ProviderInvalidRequestError(f"synthetic preflight failure for {invocation.kind}")


class MixedCurrencyProvider(FakeProvider):
    def __init__(self) -> None:
        self.invocations = 0

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        result = await super().generate(invocation)
        self.invocations += 1
        if self.invocations == 1:
            currency = "USD"
            estimated_cost_micros = 7
        else:
            currency = "CNY"
            estimated_cost_micros = 11
        return ProviderResult(
            content=result.content,
            provider=result.provider,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost_micros=estimated_cost_micros,
            cost_currency=currency,
        )


def test_dry_run_does_not_create_database_or_enable_network(
    tmp_path: Path,
    capsys: object,
) -> None:
    database_path = tmp_path / "must-not-exist.db"

    assert main(["--database", str(database_path)]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["mode"] == "dry-run"
    assert payload["execute_requested"] is False
    assert payload["network_enabled"] is False
    assert payload["paid_api_call_possible"] is False
    assert not database_path.exists()


def test_preflight_accepts_presence_only_and_cannot_print_secret() -> None:
    payload = build_preflight(
        execute=True,
        api_key_present=True,
        database_path="data/test.db",
    )

    serialized = json.dumps(payload)
    assert payload["api_key_status"] == "present"
    assert payload["network_enabled"] is True
    assert "api_key" not in payload
    assert "Bearer" not in serialized


def test_smoke_database_uses_current_alembic_migrations(tmp_path: Path) -> None:
    database_path = tmp_path / "migrated-smoke.db"

    migrate_database(database_url_for_path(database_path))

    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert revision == ("0003_model_call_trace",)
    assert {"runs", "tasks", "events", "artifacts", "model_calls"} <= tables


async def test_smoke_harness_is_bounded_and_summary_hides_content(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "fake-smoke.db"
    database_url = database_url_for_path(database_path)
    database = Database(database_url)
    await database.create_schema()
    await database.close()

    completed, processed_tasks = await run_smoke_workflow(
        database_url=database_url,
        provider=FakeProvider(),
    )
    summary = build_sanitized_summary(
        completed,
        processed_tasks=processed_tasks,
        database_path=str(database_path),
    )
    serialized = json.dumps(summary, ensure_ascii=False)

    assert summary["passed"] is True
    assert processed_tasks == MAX_MODEL_CALLS
    assert summary["run"]["status"] == "waiting_for_user"
    assert summary["run"]["current_step"] == "awaiting_interview_response"
    assert summary["run"]["model_call_count"] == MAX_MODEL_CALLS
    assert len(summary["model_calls"]) == MAX_MODEL_CALLS
    assert SYNTHETIC_SOURCE not in serialized
    assert "content_json" not in serialized
    assert "error_message" not in serialized


async def test_smoke_summary_groups_costs_by_currency(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "mixed-currency-smoke.db"
    database_url = database_url_for_path(database_path)
    database = Database(database_url)
    await database.create_schema()
    await database.close()

    completed, processed_tasks = await run_smoke_workflow(
        database_url=database_url,
        provider=MixedCurrencyProvider(),
    )
    summary = build_sanitized_summary(
        completed,
        processed_tasks=processed_tasks,
        database_path=str(database_path),
    )

    assert summary["passed"] is True
    assert summary["totals"]["estimated_costs"] == {
        "CNY": {
            "micros": 22,
            "amount": "0.000022",
        },
        "USD": {
            "micros": 7,
            "amount": "0.000007",
        },
    }
    assert "estimated_cost_micros" not in summary["totals"]
    assert "estimated_cost_usd" not in summary["totals"]


async def test_first_failure_cancels_second_call_before_provider_invocation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "failed-smoke.db"
    database_url = database_url_for_path(database_path)
    database = Database(database_url)
    await database.create_schema()
    await database.close()
    provider = FirstCallFailureProvider()

    completed, processed_tasks = await run_smoke_workflow(
        database_url=database_url,
        provider=provider,
    )

    assert completed.status == "failed"
    assert processed_tasks == 1
    assert provider.invocations == 1
    assert completed.model_call_count == 1
    children = [task for task in completed.tasks if task.parent_task_id is not None]
    assert sorted(task.status for task in children) == ["cancelled", "failed"]
