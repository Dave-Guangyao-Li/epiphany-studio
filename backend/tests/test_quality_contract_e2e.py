from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from pathlib import Path

from epiphany.quality_contract_e2e import (
    DEFAULT_FIXTURE_PATH,
    load_quality_contract_fixture,
    main,
)


def _runtime_counts(database_path: Path) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "sources",
                "runs",
                "tasks",
                "artifacts",
                "model_calls",
                "events",
            )
        }


def test_quality_contract_fixture_is_synthetic_and_crosses_threshold() -> None:
    fixture = load_quality_contract_fixture(DEFAULT_FIXTURE_PATH)
    sources = [*fixture["initial_sources"], fixture["supplemental_source"]]

    assert fixture["fixture_id"] == "m3-3-quality-contract-voice-time-capsule"
    assert fixture["creative_brief"]["target_duration_minutes"] == 10
    assert fixture["creative_brief"]["speaking_rate_chars_per_minute"] == 280
    assert fixture["creative_brief"]["scenario"] == "reflective_solo"
    assert fixture["expected"]["workflow_version"] == "v5"
    assert fixture["raw_fixture_readiness"]["initial"]["status"] == "needs_more_material"
    assert fixture["raw_fixture_readiness"]["final"]["status"] == "ready"
    assert (
        fixture["raw_fixture_readiness"]["initial"]["counts"]["available_source_char_count"]
        < fixture["raw_fixture_readiness"]["final"]["target_script_chars_min"]
    )
    assert (
        fixture["raw_fixture_readiness"]["final"]["counts"]["available_source_char_count"]
        >= fixture["raw_fixture_readiness"]["final"]["target_script_chars_min"]
    )
    assert len(fixture["initial_sources"]) == 3
    assert fixture["supplemental_source"]["source_type"] == "voice_note_transcript"
    assert all(source["metadata"]["synthetic"] is True for source in sources)
    assert all(source["metadata"]["contains_personal_data"] is False for source in sources)
    assert len({sha256(source["text"].encode()).hexdigest() for source in sources}) == len(sources)


def test_quality_contract_e2e_dry_run_creates_no_runtime_files(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    database_path = tmp_path / "dry-run.db"
    output_dir = tmp_path / "dry-run-output"
    secret = "deepseek-secret-that-must-never-be-printed"
    monkeypatch.setenv("EPIPHANY_DEEPSEEK_API_KEY", secret)

    exit_code = main(
        [
            "--provider",
            "deepseek",
            "--database",
            str(database_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    preflight = json.loads(captured.out)
    assert exit_code == 0
    assert preflight["event"] == "quality_contract_e2e.preflight"
    assert preflight["mode"] == "dry-run"
    assert preflight["network_enabled"] is False
    assert preflight["paid_api_call_possible"] is False
    assert preflight["api_key_status"] == "present"
    assert preflight["max_model_calls_per_run"] == 4
    assert preflight["creative_brief"]["target_duration_minutes"] == 10
    raw_readiness = preflight["raw_fixture_readiness_estimate"]
    assert raw_readiness["scope"].startswith("all synthetic fixture")
    assert raw_readiness["initial_status"] == "needs_more_material"
    assert raw_readiness["final_status"] == "ready"
    assert "m3_3_boundary" in preflight
    assert set(preflight["paths"]) == {
        "fixture",
        "database",
        "log",
        "report",
        "material_readiness_before",
        "material_readiness_after",
        "interview_scaffold",
        "podcast_draft",
        "show_notes",
    }
    assert secret not in captured.out
    assert secret not in captured.err
    assert not database_path.exists()
    assert not output_dir.exists()


def test_quality_contract_e2e_fake_provider_runs_restart_and_resume_journey(
    tmp_path: Path,
    capsys: object,
) -> None:
    database_path = tmp_path / "quality-contract-e2e.db"
    output_dir = tmp_path / "quality-contract-e2e-output"

    exit_code = main(
        [
            "--provider",
            "fake",
            "--execute",
            "--database",
            str(database_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    readiness_before = json.loads(
        (output_dir / "material-readiness-before.json").read_text(encoding="utf-8")
    )
    readiness_after = json.loads(
        (output_dir / "material-readiness-after.json").read_text(encoding="utf-8")
    )
    scaffold = (output_dir / "interview-scaffold.md").read_text(encoding="utf-8")
    draft = (output_dir / "podcast-draft.md").read_text(encoding="utf-8")
    notes = (output_dir / "show-notes.md").read_text(encoding="utf-8")
    log_text = (output_dir / "runtime.jsonl").read_text(encoding="utf-8")
    fixture = load_quality_contract_fixture(DEFAULT_FIXTURE_PATH)

    assert exit_code == 0, captured.err
    assert '"event": "quality_contract_e2e.preflight"' in captured.out
    assert '"event": "quality_contract_e2e.completed"' in captured.out
    assert report["passed"] is True
    assert report["failures"] == []
    assert report["runtime"]["provider"] == "fake"
    assert report["waiting_run"] == report["restarted_waiting_run"]
    assert report["waiting_run"]["workflow_version"] == "v5"
    assert report["waiting_run"]["status"] == "waiting_for_user"
    assert report["waiting_run"]["current_step"] == "awaiting_more_material"
    assert report["waiting_run"]["task_count"] == 4
    assert report["waiting_run"]["artifact_count"] == 5
    assert report["waiting_run"]["model_calls_recorded"] == 3
    assert "build_podcast_draft" not in report["waiting_run"]["task_kinds"]
    assert report["final_run"]["status"] == "succeeded"
    assert report["final_run"]["current_step"] == "complete"
    assert report["final_run"]["task_count"] == 5
    assert report["final_run"]["artifact_count"] == 8
    assert report["final_run"]["model_calls_recorded"] == 4
    assert report["final_run"]["task_statuses"] == {"succeeded": 5}
    assert readiness_before["status"] == "needs_more_material"
    assert readiness_before["counts"]["supplemental_segment_count"] == 0
    assert readiness_before["additional_source_chars_needed"] > 0
    assert readiness_before["gaps"]
    assert readiness_before["follow_up_questions"]
    assert readiness_after["status"] == "ready"
    assert readiness_after["counts"]["supplemental_segment_count"] >= 1
    assert readiness_after["additional_source_chars_needed"] == 0
    assert readiness_after["gaps"] == []
    assert report["resume"]["first_applied"] is True
    assert report["resume"]["replay_idempotent"] is True
    assert report["usage"]["input_tokens"] == 0
    assert report["usage"]["output_tokens"] == 0
    assert report["usage"]["estimated_costs"]["USD"]["micros"] == 0
    assert all(report["checks"].values())

    assert scaffold.startswith(f"# {fixture['topic']}")
    assert draft.startswith(f"# {fixture['topic']}")
    assert notes.startswith(f"# {fixture['topic']}｜Show Notes")
    assert "## 来源索引" in scaffold
    assert "## 来源索引" in draft
    assert "## 来源索引" in notes
    assert fixture["supplemental_source"]["title"] in draft
    assert fixture["supplemental_source"]["title"] in notes
    for markdown in (scaffold, draft, notes):
        assert "src_" not in markdown
        assert "seg_" not in markdown

    log_rows = [json.loads(line) for line in log_text.splitlines() if line.strip()]
    assert log_rows
    assert all(isinstance(row, dict) for row in log_rows)
    assert sum(row.get("event") == "run.waiting_for_user" for row in log_rows) == 1
    assert sum(row.get("event") == "run.resume.accepted" for row in log_rows) == 1
    assert sum(row.get("event") == "run.resume.idempotent_replay" for row in log_rows) == 1
    assert sum(row.get("event") == "workflow.editor.queued" for row in log_rows) == 1
    assert sum(row.get("event") == "workflow.editor.completed" for row in log_rows) == 1
    sources = [
        *fixture["initial_sources"],
        fixture["supplemental_source"],
    ]
    for source in sources:
        assert source["text"] not in log_text
        assert source["text"] not in json.dumps(report, ensure_ascii=False)

    counts = _runtime_counts(database_path)
    assert counts["sources"] == 4
    assert counts["runs"] == 1
    assert counts["tasks"] == 5
    assert counts["artifacts"] == 8
    assert counts["model_calls"] == 4
    assert counts["events"] == report["events"]["final"]["count"]
