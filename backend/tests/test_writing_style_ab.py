from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from epiphany.db import Database
from epiphany.editor_schemas import PodcastDraftTaskInput
from epiphany.models import Artifact, Run, Task
from epiphany.writing_style import build_writing_style_profile
from epiphany.writing_style_ab import (
    WritingStyleABSourceInvalid,
    build_arm_inputs,
    build_preflight,
    database_url_for_path,
    load_frozen_input_from_run,
    main,
)
from epiphany.writing_style_ab_schemas import FrozenWritingStyleABInput

INITIAL = {"source_id": "src_initial", "source_segment_id": "seg_initial"}
SUPPLEMENTAL = {
    "source_id": "src_supplemental",
    "source_segment_id": "seg_supplemental",
}
STYLE = {"source_id": "src_style", "source_segment_id": "seg_style"}
TOPIC = "五年后重新打开播客"


def _grounded(text: str, reference: dict[str, str] = INITIAL) -> dict[str, object]:
    return {"text": text, "source_refs": [reference]}


def _style_text() -> str:
    sentence = "我一般会先讲一个很具体的小场景，然后才慢慢说到自己当时的感受。"
    return sentence * 30


def _editor_input() -> PodcastDraftTaskInput:
    style_segments = [{**STYLE, "position": 0, "text": _style_text()}]
    profile = build_writing_style_profile(
        reference={
            "samples": [
                {
                    "source_id": STYLE["source_id"],
                    "sample_kind": "spoken_transcript",
                }
            ],
            "ownership_attested": True,
            "model_processing_consent": True,
            "usage": "style_only",
        },
        source_segments=style_segments,
    )
    assert profile is not None and profile.readiness.status == "ready"
    return PodcastDraftTaskInput.model_validate(
        {
            "task_kind": "build_podcast_draft",
            "topic": TOPIC,
            "scaffold_artifact_id": "art_scaffold",
            "submission_artifact_id": "art_submission",
            "creative_brief": {
                "target_duration_minutes": 10,
                "scenario": "reflective_solo",
                "target_audience": "喜欢个人叙事播客的人",
                "communication_goal": "解释为什么重新开始记录",
                "tone": ["真诚", "克制"],
            },
            "interview_scaffold": {
                "title": TOPIC,
                "episode_intent": _grounded("解释旧声音为什么推动了重新开始。"),
                "opening": _grounded("从重新点开旧录音的晚上讲起。"),
                "sections": [
                    {
                        "title": "重新听见",
                        "source_refs": [INITIAL],
                        "known_context": [_grounded("旧录音保留了当时的停顿。")],
                        "transition": _grounded("先回到点开文件的瞬间。"),
                        "questions": [
                            {
                                "prompt": "你先听见了什么？",
                                "purpose": "补充场景。",
                                "keywords": ["声音", "停顿"],
                                "source_refs": [INITIAL],
                            }
                        ],
                    },
                    {
                        "title": "重新开始",
                        "source_refs": [INITIAL],
                        "known_context": [_grounded("播客停了五年。")],
                        "transition": _grounded("再说为什么现在回来。"),
                        "questions": [
                            {
                                "prompt": "为什么现在重新开始？",
                                "purpose": "补充动机。",
                                "keywords": ["记录", "开始"],
                                "source_refs": [INITIAL],
                            }
                        ],
                    },
                ],
                "material_gaps": [],
                "closing": _grounded("把今天的声音留给未来。"),
            },
            "initial_source_segments": [
                {
                    **INITIAL,
                    "text": "五年前录过播客，重新点开时先听见窗外的雨声。",
                }
            ],
            "supplemental_source_segments": [
                {
                    **SUPPLEMENTAL,
                    "text": "补充口述提到声音保存了停顿、呼吸和当时的不确定。",
                }
            ],
            "writing_style_profile": profile.model_dump(mode="json"),
            "writing_style_segments": style_segments,
        }
    )


def _draft() -> dict[str, object]:
    return {
        "title": TOPIC,
        "podcast_script": {
            "opening": _grounded("前几天，我重新点开了一段五年前的录音。"),
            "sections": [
                {
                    "title": "旧声音",
                    "source_refs": [INITIAL],
                    "paragraphs": [_grounded("我先听见了窗外的雨声。")],
                },
                {
                    "title": "现在",
                    "source_refs": [SUPPLEMENTAL],
                    "paragraphs": [
                        _grounded(
                            "停顿和呼吸让我重新认出了当时的不确定。",
                            SUPPLEMENTAL,
                        )
                    ],
                },
            ],
            "closing": _grounded("所以我想重新开始记录。", SUPPLEMENTAL),
        },
        "show_notes": {
            "summary": _grounded("一段旧声音推动了一次重新开始。", SUPPLEMENTAL),
            "key_points": [
                _grounded("重听五年前的录音。"),
                _grounded("声音如何保存当时的状态。", SUPPLEMENTAL),
            ],
        },
    }


def _frozen() -> FrozenWritingStyleABInput:
    return FrozenWritingStyleABInput(
        source_run_id="run_style_ab",
        editor_task_input=_editor_input(),
        quality_config={"enabled": True, "profile": "podcast_draft_v1"},
    )


def test_preflight_proves_one_variable_and_never_enables_network(tmp_path: Path) -> None:
    preflight = build_preflight(
        frozen=_frozen(),
        max_editor_bundle_chars=50_000,
        max_editor_tokens=10_000,
        max_quality_bundle_chars=80_000,
        max_quality_tokens=6_000,
        api_key_present=True,
        database_path=tmp_path / "source.db",
    )

    assert preflight["event"] == "writing_style_ab.preflight"
    assert preflight["mode"] == "dry-run"
    assert preflight["network_enabled"] is False
    assert preflight["provider_calls_executed"] == 0
    assert preflight["only_variable_is_writing_sample"] is True
    assert preflight["treatment_reaches_editor_prompt"] is True
    assert len(preflight["common_experiment_contract_sha256"]) == 64
    assert len(set(preflight["arm_prompt_sha256"].values())) == 2
    assert preflight["planned_live_protocol"]["total_calls"] == 4
    assert preflight["planned_live_protocol"]["sample_sent_call_count"] == 3
    assert preflight["privacy"] == {
        "contains_source_text": False,
        "contains_writing_sample_text": False,
        "contains_prompt_text": False,
        "contains_api_key": False,
    }
    assert _style_text() not in str(preflight)


def test_arm_builder_changes_only_style_context() -> None:
    arms = build_arm_inputs(_frozen())

    assert arms["without_sample"].writing_style_profile is None
    assert arms["without_sample"].writing_style_segments is None
    assert arms["with_sample"].writing_style_profile is not None
    assert arms["with_sample"].writing_style_profile.readiness.status == "ready"
    assert arms["without_sample"].model_dump(
        mode="json",
        exclude={"writing_style_profile", "writing_style_segments"},
    ) == arms["with_sample"].model_dump(
        mode="json",
        exclude={"writing_style_profile", "writing_style_segments"},
    )


def test_preflight_blocks_if_style_context_does_not_reach_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "epiphany.writing_style_ab.build_editor_prompt",
        lambda **_: SimpleNamespace(messages=[]),
    )

    with pytest.raises(WritingStyleABSourceInvalid):
        build_preflight(
            frozen=_frozen(),
            max_editor_bundle_chars=50_000,
            max_editor_tokens=10_000,
            max_quality_bundle_chars=80_000,
            max_quality_tokens=6_000,
            api_key_present=False,
            database_path=tmp_path / "source.db",
        )


async def _seed_completed_v8_run(database_path: Path) -> str:
    database_url = database_url_for_path(database_path)
    database = Database(database_url)
    await database.create_schema()
    editor_input = _editor_input()
    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Run(
                    id="run_style_ab",
                    workflow_type="episode-research",
                    workflow_version="v8",
                    status="succeeded",
                    current_step="complete",
                    input_json={
                        "topic": TOPIC,
                        "source_ids": [INITIAL["source_id"]],
                        "creative_brief": editor_input.creative_brief.model_dump(mode="json"),
                        "draft_quality": {
                            "enabled": True,
                            "profile": "podcast_draft_v1",
                        },
                        "writing_style_profile": (
                            editor_input.writing_style_profile.model_dump(mode="json")
                        ),
                        "writing_style_reference": {
                            "samples": [
                                {
                                    "source_id": STYLE["source_id"],
                                    "sample_kind": "spoken_transcript",
                                }
                            ],
                            "ownership_attested": True,
                            "model_processing_consent": True,
                            "usage": "style_only",
                        },
                    },
                )
            )
            session.add(
                Artifact(
                    id="art_draft",
                    run_id="run_style_ab",
                    kind="build_podcast_draft_result",
                    content_json={
                        **_draft(),
                        "_execution": {
                            "provider": "fake",
                            "model": "fake-v1",
                            "attempt": 1,
                        },
                    },
                    idempotency_key="style-ab:draft",
                )
            )
            session.add(
                Task(
                    id="task_editor",
                    run_id="run_style_ab",
                    kind="build_podcast_draft",
                    agent_type="editor",
                    status="succeeded",
                    input_json=editor_input.model_dump(mode="json"),
                    output_artifact_id="art_draft",
                    idempotency_key="style-ab:editor",
                )
            )
        async with database.sessions() as session, session.begin():
            run = await session.get(Run, "run_style_ab")
            assert run is not None
            run.output_artifact_id = "art_draft"
    finally:
        await database.close()
    return database_url


async def test_loader_accepts_one_completed_consented_v8_editor(tmp_path: Path) -> None:
    database_url = await _seed_completed_v8_run(tmp_path / "source.db")

    loaded = await load_frozen_input_from_run(
        database_url=database_url,
        run_id="run_style_ab",
    )

    assert loaded.source_run_id == "run_style_ab"
    assert loaded.editor_task_input.writing_style_profile is not None
    assert loaded.editor_task_input.writing_style_profile.readiness.status == "ready"


@pytest.mark.parametrize(
    "tamper",
    ["missing_run_output", "run_topic", "editor_brief", "quality_disabled"],
)
async def test_loader_rejects_inconsistent_frozen_contract(
    tmp_path: Path,
    tamper: str,
) -> None:
    database_url = await _seed_completed_v8_run(tmp_path / f"{tamper}.db")
    database = Database(database_url)
    try:
        async with database.sessions() as session, session.begin():
            run = await session.get(Run, "run_style_ab")
            task = await session.get(Task, "task_editor")
            assert run is not None and task is not None
            if tamper == "missing_run_output":
                run.output_artifact_id = None
            elif tamper == "run_topic":
                run.input_json = {**run.input_json, "topic": "另一个主题"}
            elif tamper == "editor_brief":
                task.input_json = {
                    **task.input_json,
                    "creative_brief": {
                        **task.input_json["creative_brief"],
                        "target_audience": "另一个受众",
                    },
                }
            else:
                run.input_json = {
                    **run.input_json,
                    "draft_quality": {
                        **run.input_json["draft_quality"],
                        "enabled": False,
                    },
                }
    finally:
        await database.close()

    with pytest.raises(WritingStyleABSourceInvalid):
        await load_frozen_input_from_run(
            database_url=database_url,
            run_id="run_style_ab",
        )


def test_cli_blocks_missing_run_without_changing_database(
    tmp_path: Path,
    capsys: object,
) -> None:
    database_path = tmp_path / "source.db"
    database = Database(database_url_for_path(database_path))
    asyncio.run(database.create_schema())
    asyncio.run(database.close())
    database_hash_before = hashlib.sha256(database_path.read_bytes()).hexdigest()

    exit_code = main(
        [
            "--run-id",
            "run_missing",
            "--database",
            str(database_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert '"event": "writing_style_ab.blocked"' in captured.err
    assert '"network_enabled": false' in captured.err
    assert '"provider_calls_executed": 0' in captured.err
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == database_hash_before


async def test_read_only_database_rejects_writes_and_missing_paths(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing" / "source.db"
    with pytest.raises(FileNotFoundError):
        Database(database_url_for_path(missing_path), read_only=True)
    assert not missing_path.parent.exists()

    database_path = tmp_path / "source.db"
    writable = Database(database_url_for_path(database_path))
    await writable.create_schema()
    await writable.close()

    read_only = Database(database_url_for_path(database_path), read_only=True)
    try:
        async with read_only.sessions() as session:
            with pytest.raises(OperationalError):
                await session.execute(
                    text(
                        "INSERT INTO runs "
                        "(id, workflow_type, workflow_version, status, input_json, "
                        "model_call_count, next_event_sequence, created_at, updated_at) "
                        "VALUES ('run_forbidden', 'test', 'v1', 'queued', '{}', 0, 0, "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
    finally:
        await read_only.close()
