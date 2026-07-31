from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import epiphany.length_recovery_e2e as length_recovery_e2e
from epiphany.length_recovery_e2e import (
    DEFAULT_FIXTURE_PATH,
    _log_summary,
    _post_revision_plan_summary,
    _sensitive_log_fragments,
    main,
)
from epiphany.realistic_style_experiment_e2e import load_realistic_style_fixture
from epiphany.revision_schemas import REVISE_PODCAST_DRAFT
from epiphany.runtime.providers.base import ProviderResult, TaskInvocation
from epiphany.runtime.providers.fake import FakeProvider


class _NoChangeRevisionProvider(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        if invocation.kind == REVISE_PODCAST_DRAFT:
            return ProviderResult(
                content=invocation.input_json["parent_podcast_draft"],
                provider=self.name,
                model=self.model,
                input_tokens=321,
                output_tokens=123,
                estimated_cost_micros=456,
                cost_currency=self.billing_currency,
            )
        return await super().generate(invocation)


def _database_counts(database_path: Path) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("runs", "tasks", "model_calls")
        }


def test_log_validation_rejects_a_partial_generated_paragraph_leak(
    tmp_path: Path,
) -> None:
    generated = (
        "这是一段只应该存在于候选稿中的合成内容，它不应该以完整段落或局部窗口进入结构化运行日志。"
        "即使日志只泄漏了其中一小段，测试也必须稳定地发现，而不能要求整段文字完全一致。"
    )
    forbidden = _sensitive_log_fragments({"paragraph": generated})
    leaked_window = next(fragment for fragment in forbidden if 16 <= len(fragment) < len(generated))
    log_path = tmp_path / "runtime.jsonl"
    required_events = (
        "run.waiting_for_user",
        "run.resume.accepted",
        "workflow.draft_improvement.planned",
        "workflow.draft_revision.requested",
        "workflow.draft_revision.queued",
        "workflow.draft_revision.compared",
    )
    rows = [{"event": event} for event in required_events]
    rows.append({"event": "worker.task.completed", "message": leaked_window})
    log_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    summary, valid = _log_summary(
        [log_path],
        forbidden_fragments=forbidden,
    )

    assert valid is False
    assert summary["required_events_present"] is True
    assert summary["source_sample_prompt_and_key_absent"] is False


def test_preflight_uses_the_realistic_fixture_and_caps_the_full_flow_at_seven_calls(
    tmp_path: Path,
    capsys: object,
) -> None:
    database_path = tmp_path / "preflight.db"
    output_dir = tmp_path / "preflight-output"
    fixture = load_realistic_style_fixture(DEFAULT_FIXTURE_PATH)

    exit_code = main(
        [
            "--provider",
            "fake",
            "--fixture",
            str(DEFAULT_FIXTURE_PATH),
            "--database",
            str(database_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    preflight = json.loads(captured.out)
    assert exit_code == 0
    assert preflight["event"] == "length_recovery_e2e.preflight"
    assert preflight["fixture_id"] == "m3-7-realistic-persona-moving-out"
    assert fixture["fixture_id"] == preflight["fixture_id"]
    assert len(fixture["initial_sources"]) == 3
    assert len(fixture["writing_samples"]) == 4
    assert fixture["supplemental_source"]["source_type"] == "voice_note_transcript"
    assert preflight["target_duration_minutes"] == 15
    assert preflight["duration_character_range"] == {
        "minimum": 3570,
        "target": 4200,
        "maximum": 4830,
    }
    assert preflight["model_call_ceiling"] == {
        "parent": 5,
        "child_revision": 2,
        "total": 7,
        "hidden_retry": False,
    }
    assert preflight["revision"] == {
        "selected_actions": ["reuse_unused_material"],
        "new_source_count": 0,
        "lower_target_duration": False,
        "automatic_loop": False,
    }
    assert preflight["safety"]["human_action_is_simulated_explicitly"] is True
    assert not database_path.exists()
    assert not output_dir.exists()


def test_dry_run_is_read_only_and_never_prints_source_sample_or_key(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    database_path = tmp_path / "dry-run.db"
    output_dir = tmp_path / "dry-run-output"
    secret = "synthetic-deepseek-key-that-must-stay-redacted"
    monkeypatch.setenv("EPIPHANY_DEEPSEEK_API_KEY", secret)
    fixture = load_realistic_style_fixture(DEFAULT_FIXTURE_PATH)

    exit_code = main(
        [
            "--provider",
            "deepseek",
            "--editor-model",
            "deepseek-v4-flash",
            "--reviewer-model",
            "deepseek-v4-pro",
            "--database",
            str(database_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    preflight = json.loads(captured.out)
    source_text = fixture["initial_sources"][0]["text"]
    sample_text = fixture["writing_samples"][0]["source"]["text"]
    assert exit_code == 0
    assert preflight["mode"] == "dry-run"
    assert preflight["execute_requested"] is False
    assert preflight["network_enabled"] is False
    assert preflight["paid_api_call_possible"] is False
    assert preflight["api_key_status"] == "present"
    assert preflight["editor_model"] == "deepseek-v4-flash"
    assert preflight["reviewer_model"] == "deepseek-v4-pro"
    assert secret not in captured.out
    assert secret not in captured.err
    assert source_text not in captured.out
    assert sample_text not in captured.out
    assert not database_path.exists()
    assert not output_dir.exists()


def test_fake_execute_closes_one_explicit_grounded_length_recovery_loop(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    database_path = tmp_path / "length-recovery.db"
    output_dir = tmp_path / "length-recovery-output"
    secret = "synthetic-key-never-written-to-e2e-artifacts"
    monkeypatch.setenv("EPIPHANY_DEEPSEEK_API_KEY", secret)
    fixture = load_realistic_style_fixture(DEFAULT_FIXTURE_PATH)

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
    safety = json.loads((output_dir / "safety-report.json").read_text(encoding="utf-8"))
    request = json.loads((output_dir / "revision-request.json").read_text(encoding="utf-8"))
    parent_draft = (output_dir / "parent-podcast-draft.md").read_text(encoding="utf-8")
    child_draft = (output_dir / "child-podcast-draft.md").read_text(encoding="utf-8")
    parent_log = (output_dir / "runtime.jsonl").read_text(encoding="utf-8")
    revision_log = (output_dir / "revision-runtime.jsonl").read_text(encoding="utf-8")
    combined_logs = f"{parent_log}\n{revision_log}"

    assert exit_code == 1, captured.err
    assert '"event": "length_recovery_e2e.preflight"' in captured.out
    assert '"event": "length_recovery_e2e.completed"' in captured.out
    assert report["passed"] is False
    assert report["workflow_passed"] is True
    assert report["content_acceptance_passed"] is False
    assert report["failures"]
    assert all(report["workflow_checks"].values())
    assert report["content_checks"]["child_reaches_duration_range"] is False

    assert report["workflow"]["automatic_revision_count"] == 0
    assert report["workflow"]["explicit_revision_count"] == 1
    assert report["post_revision_next_action"] == "reuse_then_supplement"
    assert report["child_plan"]["plan"]["duration_resolution"] == "reuse_then_supplement"
    assert {
        "reuse_unused_material",
        "add_supplemental_material",
        "lower_target_duration",
    } <= set(report["child_plan"]["next_action"]["option_kinds"])
    assert 3 <= len(report["child_plan"]["next_action"]["targeted_questions"]) <= 6
    assert report["child_plan"]["next_action"]["requires_human_action"] is True
    assert report["child_plan"]["next_action"]["automatic_follow_up_revision_created"] is False
    assert request["submission_id"] == "m3-8-realistic-length-recovery-v1"
    assert request["selected_actions"] == ["reuse_unused_material"]
    assert request["source_ids"] == []
    assert request["target_duration_minutes"] is None
    assert request["child_run_id"] == report["child"]["id"]
    assert report["parent"]["model_calls_recorded"] == 5
    assert report["child"]["model_calls_recorded"] == 2
    assert report["runtime"]["model_call_ceiling"] == 7
    assert report["runtime"]["hidden_retry"] is False
    assert _database_counts(database_path) == {
        "runs": 2,
        "tasks": 8,
        "model_calls": 7,
    }

    quality = report["quality"]
    assert quality["parent"]["script_character_count"] < quality["minimum_script_character_count"]
    assert quality["child"]["script_character_count"] < quality["minimum_script_character_count"]
    assert quality["script_character_delta"] > 0
    assert quality["parent"]["duration_status"] == "blocker"
    assert quality["child"]["duration_status"] == "blocker"
    assert report["material_utilization"]["newly_used_priority_ref_count"] > 0
    assert report["material_utilization"]["all_material_used_required"] is False

    assert parent_draft != child_draft
    assert len(child_draft) > len(parent_draft)
    for markdown in (parent_draft, child_draft):
        assert "src_" not in markdown
        assert "seg_" not in markdown
        assert "## 来源索引" in markdown
    for required_output in (
        "improvement-plan.json",
        "post-revision-improvement-plan.json",
        "parent-quality-report.json",
        "child-quality-report.json",
        "revision-comparison.json",
        "parent-show-notes.md",
        "child-show-notes.md",
        "parent-quality-report.md",
        "child-quality-report.md",
    ):
        assert (output_dir / required_output).exists()

    assert safety["passed"] is True
    assert safety["automatic_revision_count"] == 0
    assert safety["explicit_revision_count"] == 1
    assert report["logs"]["required_events_present"] is True
    assert report["logs"]["source_sample_prompt_and_key_absent"] is True
    log_rows = [json.loads(line) for line in combined_logs.splitlines() if line.strip()]
    assert log_rows
    assert all(isinstance(row, dict) for row in log_rows)
    assert sum(row.get("event") == "workflow.draft_revision.requested" for row in log_rows) == 1
    assert (
        sum(row.get("event") == "workflow.draft_revision.idempotent_replay" for row in log_rows)
        == 1
    )
    assert sum(row.get("event") == "workflow.draft_revision.queued" for row in log_rows) == 1
    assert secret not in combined_logs
    for source in [
        *fixture["initial_sources"],
        fixture["supplemental_source"],
    ]:
        assert source["text"] not in combined_logs
    for sample in fixture["writing_samples"]:
        assert sample["source"]["text"] not in combined_logs


def test_post_revision_plan_summary_marks_an_in_range_child_as_done() -> None:
    summary = _post_revision_plan_summary(
        {
            "plan": {
                "duration_resolution": "not_needed",
                "options": [],
                "targeted_questions": [
                    {"prompt": "问题一"},
                    {"prompt": "问题二"},
                    {"prompt": "问题三"},
                ],
            }
        }
    )

    assert summary == {
        "duration_resolution": "not_needed",
        "option_kinds": [],
        "targeted_questions": [
            {"prompt": "问题一"},
            {"prompt": "问题二"},
            {"prompt": "问题三"},
        ],
        "requires_human_action": False,
        "automatic_follow_up_revision_created": False,
    }


def test_failed_child_preserves_real_error_usage_and_safety_without_child_outputs(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    database_path = tmp_path / "failed-child.db"
    output_dir = tmp_path / "failed-child-output"
    provider = _NoChangeRevisionProvider()
    monkeypatch.setattr(
        length_recovery_e2e,
        "build_realistic_provider",
        lambda **_kwargs: provider,
    )

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
    safety = json.loads((output_dir / "safety-report.json").read_text(encoding="utf-8"))
    revision_rows = [
        json.loads(line)
        for line in (output_dir / "revision-runtime.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert exit_code == 1
    assert report["event"] == "length_recovery_e2e.completed"
    assert report["passed"] is False
    assert report["workflow_passed"] is False
    assert report["content_acceptance_passed"] is False
    assert report["child"]["status"] == "failed"
    assert report["child"]["terminal_error_code"] == "podcast_revision_no_change"
    assert report["child"]["tasks"] == [
        {
            "id": report["child"]["tasks"][0]["id"],
            "kind": "revise_podcast_draft",
            "status": "failed",
            "attempt": 1,
            "max_attempts": 1,
            "output_artifact_id": None,
            "error_code": "podcast_revision_no_change",
        }
    ]
    assert report["child"]["model_calls_recorded"] == 1
    assert report["workflow"]["reviewer_requested"] is False
    assert report["workflow"]["comparison_requested"] is False
    assert report["workflow"]["comparison_artifact_id"] is None
    assert report["workflow"]["child_improvement_plan_requested"] is False
    assert report["post_revision_next_action"] is None
    assert report["child_plan"] is None
    assert report["quality"]["parent"]["script_character_count"] > 0
    assert report["quality"]["child"] == {
        "status": "not_evaluated",
        "reason": "child_run_failed_before_draft_commit",
        "terminal_error_code": "podcast_revision_no_change",
    }
    assert report["quality"]["script_character_delta"] is None
    assert report["usage"]["parent"]["model_call_count"] == 5
    assert report["usage"]["child"]["model_call_count"] == 1
    assert report["usage"]["total"]["model_call_count"] == 6
    assert report["usage"]["child"]["input_tokens"] == 321
    assert report["usage"]["child"]["output_tokens"] == 123
    assert report["usage"]["child"]["estimated_costs"] == {
        "USD": {"micros": 456, "amount": "0.000456"}
    }
    assert report["runtime"]["hidden_retry"] is False
    assert report["workflow_checks"]["child_terminal_failure_preserved"] is True
    assert report["workflow_checks"]["child_execution_is_one_call_without_retry"] is True
    assert report["workflow_checks"]["reviewer_and_comparison_not_requested"] is True
    assert report["workflow_checks"]["logs_structured_and_redacted"] is True
    assert report["workflow_checks"]["child_revision_succeeded"] is False
    assert report["content_checks"] == {"child_content_acceptance_available": False}
    assert report["workflow_failures"] == ["child_revision_succeeded"]
    assert report["content_failures"] == ["child_content_acceptance_available"]
    child_id = report["child"]["id"]
    assert not any(row.get("event") == "workflow.draft_revision.compared" for row in revision_rows)
    assert not any(
        row.get("path")
        in {
            f"/runs/{child_id}/quality-report",
            f"/runs/{child_id}/improvement-plan",
            f"/runs/{child_id}/revision-comparison",
            f"/runs/{child_id}/exports/podcast-draft.md",
            f"/runs/{child_id}/exports/show-notes.md",
            f"/runs/{child_id}/exports/quality-report.md",
        }
        for row in revision_rows
    )
    assert "artifact_not_unique" not in json.dumps(report)
    assert report["outputs"]["comparison"] is None
    assert report["outputs"]["child_quality_json"] is None
    assert report["outputs"]["child_improvement_plan"] is None
    assert set(report["outputs"]["markdown"]) == {
        "parent-podcast-draft",
        "parent-show-notes",
        "parent-quality-report",
    }
    for absent_output in (
        "child-podcast-draft.md",
        "child-show-notes.md",
        "child-quality-report.md",
        "child-quality-report.json",
        "revision-comparison.json",
        "post-revision-improvement-plan.json",
    ):
        assert not (output_dir / absent_output).exists()
    assert _database_counts(database_path) == {
        "runs": 2,
        "tasks": 7,
        "model_calls": 6,
    }
    assert safety["passed"] is True
    assert safety["source_sample_prompt_and_key_absent"] is True
    assert safety["child_status"] == "failed"
    assert safety["child_terminal_error_code"] == "podcast_revision_no_change"
    assert safety["model_call_count"] == 6
    assert safety["hidden_retry"] is False
    assert '"event": "length_recovery_e2e.completed"' in captured.out
    assert "podcast_revision_no_change" not in captured.err
