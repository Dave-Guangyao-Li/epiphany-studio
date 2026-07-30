from __future__ import annotations

import json

from epiphany.guided_revision_e2e import (
    DEFAULT_FIXTURE_PATH,
    GuidedRevisionPaths,
    execute_fake_e2e,
    load_guided_revision_fixture,
    main,
)


def test_guided_revision_e2e_dry_run_is_offline_and_creates_no_runtime_files(
    tmp_path,
    capsys,
) -> None:
    database_path = tmp_path / "dry-run.db"
    output_dir = tmp_path / "dry-run-output"

    exit_code = main(
        [
            "--database",
            str(database_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    preflight = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert preflight["event"] == "guided_revision_e2e.preflight"
    assert preflight["mode"] == "dry-run"
    assert preflight["provider"] == "fake"
    assert preflight["network_enabled"] is False
    assert preflight["paid_api_call_possible"] is False
    assert preflight["feedback_origin"] == "synthetic_test"
    assert database_path.exists() is False
    assert output_dir.exists() is False


async def test_guided_revision_e2e_exercises_the_complete_zero_cost_journey(
    tmp_path,
) -> None:
    paths = GuidedRevisionPaths(
        fixture=DEFAULT_FIXTURE_PATH,
        database=tmp_path / "guided-revision.db",
        output_dir=tmp_path / "artifacts",
    )

    report = await execute_fake_e2e(
        fixture=load_guided_revision_fixture(paths.fixture),
        paths=paths,
    )

    assert report["passed"] is True
    assert report["failures"] == []
    assert all(report["checks"].values())
    assert report["parent"]["model_calls_recorded"] == 5
    assert report["child"]["model_calls_recorded"] == 2
    assert report["child"]["workflow_type"] == "podcast-revision"
    assert paths.log.exists()
    assert paths.json_artifact("improvement-plan").exists()
    assert paths.json_artifact("revision-comparison").exists()
    assert paths.markdown("parent", "interview-scaffold").exists()
    assert paths.markdown("parent", "podcast-draft").exists()
    assert paths.markdown("child", "podcast-draft").exists()
    assert paths.markdown("child", "quality-report").exists()
