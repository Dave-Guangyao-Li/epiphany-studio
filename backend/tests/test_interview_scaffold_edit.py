from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from epiphany.db import Database
from epiphany.models import Artifact
from epiphany.runtime.worker import Worker
from epiphany.scaffold_edit_schemas import (
    INTERVIEW_SCAFFOLD_HUMAN_EDIT_KIND,
    INTERVIEW_SCAFFOLD_RESULT_KIND,
    InterviewScaffoldEditRequest,
    InterviewScaffoldGroundingChanged,
)
from epiphany.services import (
    InterviewScaffoldEditConflict,
    InterviewScaffoldEditNotAllowed,
    RunService,
)
from epiphany.source_service import SourceService

_CREATIVE_BRIEF = {
    "target_duration_minutes": 15,
    "speaking_rate_chars_per_minute": 280,
    "scenario": "reflective_solo",
    "target_audience": "正在经历人生转折、想重新开始记录的普通听众",
    "communication_goal": "用具体经历解释为什么重新开始记录",
    "tone": ["真诚", "克制", "自然口语"],
    "must_include": ["重新开始"],
    "avoid_patterns": ["空泛排比", "强行金句"],
}


def _factual_material(prefix: str, *, paragraph_count: int, detail_count: int) -> str:
    return "\n\n".join(
        (
            f"{prefix}第{paragraph_index}段。"
            + "".join(
                (
                    f"那天我先注意到细节{paragraph_index}-{detail_index}，"
                    "接着记下一个动作、一句没说完的话和当时身体里的反应。"
                )
                for detail_index in range(detail_count)
            )
            + "现在回头看，我仍然能区分当时的事实和后来才形成的解释。"
        )
        for paragraph_index in range(paragraph_count)
    )


async def _run_to_scaffold(
    database: Database,
    service: RunService,
    worker: Worker,
) -> tuple[str, str, dict]:
    """Create a Run and stop at the material-readiness checkpoint.

    A ``build_interview_scaffold_result`` Artifact exists at that point. Returns
    (run_id, scaffold_artifact_id, base_scaffold_content_without_execution).
    """

    imported = await SourceService(database).import_text(
        title="脚手架编辑·初始素材",
        source_type="journal",
        text=_factual_material("初始事实", paragraph_count=5, detail_count=7),
        metadata={"synthetic": True, "contains_personal_data": False, "test": "scaffold_edit"},
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始记录生活",
            "source_ids": [imported.source.id],
            "creative_brief": _CREATIVE_BRIEF,
        },
    )
    assert await worker.run_until_idle() == 3
    waiting = await service.get_run(created.id)
    assert waiting.status == "waiting_for_user"

    async with database.sessions() as session:
        scaffold = (
            await session.execute(
                select(Artifact).where(
                    Artifact.run_id == created.id,
                    Artifact.kind == INTERVIEW_SCAFFOLD_RESULT_KIND,
                )
            )
        ).scalar_one()
        content = {
            key: value for key, value in scaffold.content_json.items() if key != "_execution"
        }
        return created.id, scaffold.id, content


async def test_edit_creates_immutable_human_version_and_export_reflects_it(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    run_id, scaffold_id, base_content = await _run_to_scaffold(database, service, worker)
    original_opening = base_content["opening"]["text"]

    edited = copy.deepcopy(base_content)
    edited["opening"]["text"] = "人工改写后的开场白，只调整了文字，没有动引用。"
    edited["sections"][0]["questions"][0]["prompt"] = "人工修改后的第一个问题，仍然对应同一段素材。"

    response = await service.save_interview_scaffold_edit(
        run_id,
        request=InterviewScaffoldEditRequest(
            submission_id="scaffold-edit-1",
            base_artifact_id=scaffold_id,
            scaffold=edited,
        ),
    )

    assert response.idempotent_replay is False
    assert response.edit.base_artifact_id == scaffold_id
    assert response.edit.editor_origin == "human"
    assert response.artifact.kind == INTERVIEW_SCAFFOLD_HUMAN_EDIT_KIND

    # The original AI scaffold Artifact must never be mutated.
    async with database.sessions() as session:
        base = await session.get(Artifact, scaffold_id)
        assert base is not None
        assert base.content_json["opening"]["text"] == original_opening
        edits = (
            (
                await session.execute(
                    select(Artifact).where(
                        Artifact.run_id == run_id,
                        Artifact.kind == INTERVIEW_SCAFFOLD_HUMAN_EDIT_KIND,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(edits) == 1

    # The export reflects the latest human-edited version.
    markdown = await service.export_interview_scaffold_markdown(run_id)
    assert "人工改写后的开场白，只调整了文字，没有动引用。" in markdown
    assert "人工修改后的第一个问题，仍然对应同一段素材。" in markdown
    assert original_opening not in markdown


async def test_edit_rejects_changed_source_reference(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    run_id, scaffold_id, base_content = await _run_to_scaffold(database, service, worker)

    edited = copy.deepcopy(base_content)
    edited["opening"]["source_refs"][0]["source_segment_id"] = "seg_not_in_bundle"

    with pytest.raises(InterviewScaffoldGroundingChanged):
        await service.save_interview_scaffold_edit(
            run_id,
            request=InterviewScaffoldEditRequest(
                submission_id="scaffold-edit-bad-ref",
                base_artifact_id=scaffold_id,
                scaffold=edited,
            ),
        )

    async with database.sessions() as session:
        edits = (
            (
                await session.execute(
                    select(Artifact).where(
                        Artifact.run_id == run_id,
                        Artifact.kind == INTERVIEW_SCAFFOLD_HUMAN_EDIT_KIND,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert edits == []


async def test_edit_rejects_title_change(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    run_id, scaffold_id, base_content = await _run_to_scaffold(database, service, worker)

    edited = copy.deepcopy(base_content)
    edited["title"] = "一个完全不同、偏离 topic 的标题"

    with pytest.raises(InterviewScaffoldGroundingChanged):
        await service.save_interview_scaffold_edit(
            run_id,
            request=InterviewScaffoldEditRequest(
                submission_id="scaffold-edit-bad-title",
                base_artifact_id=scaffold_id,
                scaffold=edited,
            ),
        )


async def test_edit_rejects_added_citation(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    run_id, scaffold_id, base_content = await _run_to_scaffold(database, service, worker)

    # Append an extra, structurally valid citation to the opening. Adding a
    # citation changes grounding and must be rejected even if the source_id is
    # one already used elsewhere.
    existing_source_id = base_content["opening"]["source_refs"][0]["source_id"]
    edited = copy.deepcopy(base_content)
    edited["opening"]["source_refs"].append(
        {"source_id": existing_source_id, "source_segment_id": "seg_extra_citation"}
    )

    with pytest.raises(InterviewScaffoldGroundingChanged):
        await service.save_interview_scaffold_edit(
            run_id,
            request=InterviewScaffoldEditRequest(
                submission_id="scaffold-edit-add-citation",
                base_artifact_id=scaffold_id,
                scaffold=edited,
            ),
        )


async def test_edit_is_idempotent_and_detects_conflict(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    run_id, scaffold_id, base_content = await _run_to_scaffold(database, service, worker)

    edited = copy.deepcopy(base_content)
    edited["closing"]["text"] = "人工改写后的收尾，第一版。"
    request = InterviewScaffoldEditRequest(
        submission_id="scaffold-edit-idem",
        base_artifact_id=scaffold_id,
        scaffold=edited,
    )
    created = await service.save_interview_scaffold_edit(run_id, request=request)
    assert created.idempotent_replay is False

    replay = await service.save_interview_scaffold_edit(run_id, request=request)
    assert replay.idempotent_replay is True
    assert replay.artifact.id == created.artifact.id

    conflicting = copy.deepcopy(base_content)
    conflicting["closing"]["text"] = "人工改写后的收尾，内容不同的第二版。"
    with pytest.raises(InterviewScaffoldEditConflict):
        await service.save_interview_scaffold_edit(
            run_id,
            request=InterviewScaffoldEditRequest(
                submission_id="scaffold-edit-idem",
                base_artifact_id=scaffold_id,
                scaffold=conflicting,
            ),
        )


async def test_edit_rejects_mismatched_base_artifact_id(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    run_id, _scaffold_id, base_content = await _run_to_scaffold(database, service, worker)

    with pytest.raises(InterviewScaffoldEditNotAllowed):
        await service.save_interview_scaffold_edit(
            run_id,
            request=InterviewScaffoldEditRequest(
                submission_id="scaffold-edit-wrong-base",
                base_artifact_id="art_does_not_match",
                scaffold=copy.deepcopy(base_content),
            ),
        )
