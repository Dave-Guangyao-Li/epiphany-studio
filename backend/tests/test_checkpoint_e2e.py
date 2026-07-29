from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from pathlib import Path

from epiphany.checkpoint_e2e import (
    DEFAULT_FIXTURE_PATH,
    _has_readable_citations,
    _read_log_summary,
    _safe_run_summary,
    load_fixture,
    main,
)


def _runtime_counts(database_path: Path) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sources", "runs", "tasks", "artifacts", "model_calls", "events")
        }


def test_readable_citation_check_rejects_unknown_and_escaped_internal_ids() -> None:
    valid_markdown = "来源：[S1]\n\n## 来源索引\n\n- [S1] 《测试素材》片段 1\n"
    assert _has_readable_citations(valid_markdown)

    for leaked_identifier in (
        "src_unknown_identifier",
        "seg_unknown_identifier",
        r"src\_escaped_identifier",
        r"seg\_escaped_identifier",
    ):
        assert not _has_readable_citations(f"{valid_markdown}\n模型误输出 {leaked_identifier}。\n")


def test_checkpoint_fixture_is_synthetic_valid_and_unique() -> None:
    fixture = load_fixture(DEFAULT_FIXTURE_PATH)
    sources = [*fixture["initial_sources"], fixture["supplemental_source"]]
    initial_roles = {source["metadata"]["role"] for source in fixture["initial_sources"]}

    assert fixture["fixture_id"] == "m3-1-voice-time-capsule"
    assert fixture["topic"]
    assert fixture["submission_id"]
    assert len(fixture["initial_sources"]) == 3
    assert fixture["supplemental_source"]["source_type"] == "voice_note_transcript"
    assert initial_roles == {
        "timeline_and_scenes",
        "reflection_and_principles",
        "episode_zero_draft",
    }
    assert fixture["supplemental_source"]["metadata"]["role"] == "interview_response"
    assert all(len(source["text"]) >= 600 for source in fixture["initial_sources"])
    assert len(fixture["supplemental_source"]["text"]) >= 800
    assert all(source["text"].count("\n\n") >= 4 for source in fixture["initial_sources"])
    assert fixture["supplemental_source"]["text"].count("\n\n") >= 5
    assert all(source["text"].strip() for source in sources)
    assert all(source["metadata"]["synthetic"] is True for source in sources)
    assert all(source["metadata"]["contains_personal_data"] is False for source in sources)
    assert len({sha256(source["text"].encode()).hexdigest() for source in sources}) == len(sources)


def test_checkpoint_e2e_dry_run_creates_no_runtime_files(
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
    assert preflight["event"] == "checkpoint_e2e.preflight"
    assert preflight["mode"] == "dry-run"
    assert preflight["network_enabled"] is False
    assert preflight["paid_api_call_possible"] is False
    assert preflight["api_key_status"] == "present"
    assert preflight["max_model_calls_per_run"] == 4
    assert preflight["max_editor_output_tokens"] == 6_000
    assert preflight["max_editor_bundle_chars"] == 32_000
    assert "m3_2_boundary" in preflight
    assert set(preflight["paths"]) == {
        "fixture",
        "database",
        "log",
        "report",
        "interview_scaffold",
        "podcast_draft",
        "show_notes",
    }
    assert secret not in captured.out
    assert secret not in captured.err
    assert not database_path.exists()
    assert not output_dir.exists()


def test_checkpoint_e2e_fake_provider_runs_full_http_journey(
    tmp_path: Path,
    capsys: object,
) -> None:
    database_path = tmp_path / "checkpoint-e2e.db"
    output_dir = tmp_path / "checkpoint-e2e-artifacts"

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
    scaffold = (output_dir / "interview-scaffold.md").read_text(encoding="utf-8")
    podcast_draft = (output_dir / "podcast-draft.md").read_text(encoding="utf-8")
    show_notes = (output_dir / "show-notes.md").read_text(encoding="utf-8")
    log_text = (output_dir / "runtime.jsonl").read_text(encoding="utf-8")
    fixture = load_fixture(DEFAULT_FIXTURE_PATH)

    assert exit_code == 0
    assert '"event": "checkpoint_e2e.preflight"' in captured.out
    assert '"event": "checkpoint_e2e.completed"' in captured.out
    assert report["passed"] is True
    assert report["failures"] == []
    assert report["runtime"]["provider"] == "fake"
    assert report["waiting_run"]["status"] == "waiting_for_user"
    assert report["waiting_run"]["current_step"] == "awaiting_interview_response"
    assert report["waiting_run"]["task_count"] == 4
    assert report["waiting_run"]["artifact_count"] == 4
    assert report["waiting_run"]["model_calls_recorded"] == 3
    assert report["final_run"]["status"] == "succeeded"
    assert report["final_run"]["current_step"] == "complete"
    assert report["final_run"]["workflow_version"] == "v4"
    assert report["final_run"]["task_count"] == 5
    assert report["final_run"]["artifact_count"] == 6
    assert report["final_run"]["model_calls_recorded"] == 4
    assert report["final_run"]["task_statuses"] == {"succeeded": 5}
    assert "build_podcast_draft" in report["final_run"]["task_kinds"]
    assert "build_podcast_draft_result" in report["final_run"]["artifact_kinds"]
    assert report["resume"]["first_applied"] is True
    assert report["resume"]["replay_idempotent"] is True
    assert report["events"]["after_resume"]["count"] == (
        report["events"]["before_resume"]["count"] + 10
    )
    assert report["usage"]["input_tokens"] == 0
    assert report["usage"]["output_tokens"] == 0
    assert report["usage"]["estimated_costs"]["USD"]["micros"] == 0
    scaffold_report = report["markdown"]["interview_scaffold"]
    draft_report = report["markdown"]["podcast_draft"]
    notes_report = report["markdown"]["show_notes"]
    assert scaffold_report["stable_after_resume"] is True
    assert scaffold_report["sha256"] == sha256(scaffold.encode()).hexdigest()
    assert draft_report["sha256"] == sha256(podcast_draft.encode()).hexdigest()
    assert notes_report["sha256"] == sha256(show_notes.encode()).hexdigest()
    assert all(scaffold_report["structure"].values())
    assert all(draft_report["structure"].values())
    assert all(notes_report["structure"].values())

    assert scaffold.startswith(f"# {fixture['topic']}")
    assert "## 开场" in scaffold
    assert "### 采访问题" in scaffold
    assert scaffold.count("### 采访问题") == 3
    assert "来源：[S" in scaffold
    assert "## 来源索引" in scaffold
    assert "《合成素材A｜五年时间线与重听旧录音的晚上》" in scaffold
    assert "2026年7月12日" in scaffold
    assert "重新听见过去的自己" in scaffold
    assert "生活习惯" in scaffold

    assert podcast_draft.startswith(f"# {fixture['topic']}")
    assert "## 开场" in podcast_draft
    assert "## 收束" in podcast_draft
    assert "## 来源索引" in podcast_draft
    assert "《合成补充口述｜重听时刻、停更原因与重新开始的边界》" in podcast_draft
    assert "房间特别安静" in podcast_draft
    assert show_notes.startswith(f"# {fixture['topic']}｜Show Notes")
    assert "## 本期内容" in show_notes
    assert "## 来源索引" in show_notes
    assert "《合成补充口述｜重听时刻、停更原因与重新开始的边界》" in show_notes
    assert "房间特别安静" in show_notes
    for markdown in (scaffold, podcast_draft, show_notes):
        assert "src_" not in markdown
        assert "seg_" not in markdown
        assert "A deterministic" not in markdown
        assert "Candidate moment" not in markdown
        assert markdown.count("“") == markdown.count("”")
        assert markdown.count("《") == markdown.count("》")

    log_rows = [json.loads(line) for line in log_text.splitlines() if line.strip()]
    assert log_rows
    assert all(isinstance(row, dict) for row in log_rows)
    assert any(row.get("event") == "run.waiting_for_user" for row in log_rows)
    assert any(row.get("event") == "run.resume.accepted" for row in log_rows)
    assert any(row.get("event") == "run.resume.idempotent_replay" for row in log_rows)
    assert any(row.get("event") == "workflow.editor.queued" for row in log_rows)
    assert any(row.get("event") == "workflow.editor.completed" for row in log_rows)
    assert any(row.get("request_id") == "req_e2e_create_run" for row in log_rows)
    for source in [*fixture["initial_sources"], fixture["supplemental_source"]]:
        assert source["text"] not in log_text
        assert source["text"] not in json.dumps(report, ensure_ascii=False)

    counts = _runtime_counts(database_path)
    assert counts["sources"] == 4
    assert counts["runs"] == 1
    assert counts["tasks"] == 5
    assert counts["artifacts"] == 6
    assert counts["model_calls"] == 4
    assert counts["events"] == report["events"]["after_resume"]["count"]


def test_checkpoint_e2e_rejects_database_with_active_task_without_new_work(
    tmp_path: Path,
    capsys: object,
) -> None:
    database_path = tmp_path / "reused.db"
    first_output_dir = tmp_path / "first-run"
    blocked_output_dir = tmp_path / "blocked-run"

    assert (
        main(
            [
                "--provider",
                "fake",
                "--execute",
                "--database",
                str(database_path),
                "--output-dir",
                str(first_output_dir),
            ]
        )
        == 0
    )
    capsys.readouterr()

    with sqlite3.connect(database_path) as connection:
        task_id = connection.execute("SELECT id FROM tasks ORDER BY created_at LIMIT 1").fetchone()[
            0
        ]
        connection.execute(
            """
            UPDATE tasks
            SET status = 'queued',
                output_artifact_id = NULL,
                lease_token = NULL,
                lease_expires_at = NULL
            WHERE id = ?
            """,
            (task_id,),
        )
        connection.commit()

    counts_before = _runtime_counts(database_path)
    exit_code = main(
        [
            "--provider",
            "fake",
            "--execute",
            "--database",
            str(database_path),
            "--output-dir",
            str(blocked_output_dir),
        ]
    )

    captured = capsys.readouterr()
    report = json.loads((blocked_output_dir / "report.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert report["passed"] is False
    assert report["stage"] == "database_preflight"
    assert report["error_code"] == "database_has_active_tasks"
    assert report["evidence"] == {"active_task_count": 1}
    assert report["logs"] is None
    assert not (blocked_output_dir / "runtime.jsonl").exists()
    assert _runtime_counts(database_path) == counts_before
    assert counts_before["runs"] == 1
    assert counts_before["model_calls"] == 4
    assert "database_has_active_tasks" in captured.err


def test_checkpoint_e2e_forces_info_acceptance_logs_even_when_env_is_error(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    database_path = tmp_path / "log-level.db"
    output_dir = tmp_path / "log-level-output"
    monkeypatch.setenv("EPIPHANY_LOG_LEVEL", "ERROR")

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
    log_rows = [
        json.loads(line)
        for line in (output_dir / "runtime.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert exit_code == 0
    assert report["passed"] is True
    assert report["logs"]["required_events_present"] is True
    assert any(
        row.get("level") == "INFO" and row.get("event") == "run.waiting_for_user"
        for row in log_rows
    )
    assert '"event": "checkpoint_e2e.completed"' in captured.out


def test_safe_run_summary_keeps_task_error_codes_without_task_payloads() -> None:
    summary = _safe_run_summary(
        {
            "id": "run_test",
            "workflow_type": "episode-research",
            "workflow_version": "v4",
            "status": "failed",
            "current_step": "research",
            "tasks": [
                {
                    "id": "task_test",
                    "kind": "timeline_research",
                    "status": "failed",
                    "attempt": 1,
                    "max_attempts": 1,
                    "output_artifact_id": None,
                    "error_code": "provider_network_error",
                    "input_json": {"source_text": "must-not-enter-safe-summary"},
                }
            ],
            "artifacts": [],
            "model_calls": [],
        }
    )

    assert summary["tasks"] == [
        {
            "id": "task_test",
            "kind": "timeline_research",
            "status": "failed",
            "attempt": 1,
            "max_attempts": 1,
            "output_artifact_id": None,
            "error_code": "provider_network_error",
        }
    ]
    assert summary["task_statuses"] == {"failed": 1}
    assert "must-not-enter-safe-summary" not in json.dumps(summary)


def test_log_summary_aggregates_error_codes_without_exposing_forbidden_text(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "runtime.jsonl"
    rows = [
        {"event": "run.waiting_for_user", "level": "INFO"},
        {"event": "run.resume.accepted", "level": "INFO"},
        {"event": "run.resume.idempotent_replay", "level": "INFO"},
        {"event": "workflow.editor.queued", "level": "INFO"},
        {"event": "workflow.editor.completed", "level": "INFO"},
        {
            "event": "task.failed",
            "level": "ERROR",
            "error_code": "provider_network_error",
        },
        {
            "event": "task.failed",
            "level": "ERROR",
            "error_code": "provider_network_error",
        },
        {
            "event": "task.failed",
            "level": "ERROR",
            "error_code": "task_output_invalid",
        },
    ]
    log_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    summary, passed = _read_log_summary(
        log_path,
        forbidden_texts=["synthetic source text that must remain private"],
        provider_name="fake",
    )

    assert passed is True
    assert summary["error_code_counts"] == {
        "provider_network_error": 2,
        "task_output_invalid": 1,
    }
    assert summary["source_text_absent"] is True
