from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from epiphany.db import Database
from epiphany.draft_quality import build_deterministic_quality_facts
from epiphany.draft_quality_schemas import (
    DRAFT_QUALITY_FORMULA_VERSION,
    DRAFT_QUALITY_RULES_VERSION,
    LEGACY_DRAFT_QUALITY_FORMULA_VERSION,
    LEGACY_DRAFT_QUALITY_RULES_VERSION,
    REVIEW_PODCAST_DRAFT,
    STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION,
    STYLE_AWARE_MODEL_REVIEW_TASK_VERSION,
    DeterministicDraftQualityResult,
)
from epiphany.editor_schemas import BUILD_PODCAST_DRAFT
from epiphany.models import Artifact, Run, Task
from epiphany.runtime.orchestrator import (
    LEGACY_QUALITY_REVIEW_WORKFLOW_VERSION,
    QUALITY_REVIEW_WORKFLOW_VERSION,
    Orchestrator,
)
from epiphany.runtime.providers import (
    FakeProvider,
    ProviderAuthenticationError,
    ProviderResult,
    RetryableProviderError,
    TaskInvocation,
)
from epiphany.runtime.quality_prompts import (
    LEGACY_QUALITY_REVIEW_PROMPT_VERSION,
    build_quality_review_prompt,
)
from epiphany.runtime.worker import Worker
from epiphany.services import RunService
from epiphany.source_service import SourceService


def _material(prefix: str, *, paragraph_count: int, detail_count: int) -> str:
    return "\n\n".join(
        (
            f"{prefix}第{paragraph_index}段："
            + "".join(
                (
                    f"那天的细节{paragraph_index}-{detail_index}包括一个动作、"
                    "一句没有说完的话和当时的身体感受。"
                )
                for detail_index in range(detail_count)
            )
        )
        for paragraph_index in range(paragraph_count)
    )


async def _import_material(
    database: Database,
    *,
    title: str,
    text: str,
) -> str:
    imported = await SourceService(database).import_text(
        title=title,
        source_type="voice_note_transcript",
        text=text,
        metadata={
            "synthetic": True,
            "contains_personal_data": False,
            "test": "draft_quality_workflow",
        },
    )
    return imported.source.id


async def _create_quality_run(
    database: Database,
    service: RunService,
    *,
    force_legacy_v6: bool = False,
) -> str:
    initial_source_id = await _import_material(
        database,
        title="质量工作流初始素材",
        text=_material("初始记录", paragraph_count=4, detail_count=8),
    )
    created = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "五年后重新开始记录生活",
            "source_ids": [initial_source_id],
            "creative_brief": {
                "target_duration_minutes": 10,
                "speaking_rate_chars_per_minute": 280,
                "scenario": "reflective_solo",
                "target_audience": "正在经历人生转折的普通听众",
                "communication_goal": "用具体经历解释为什么重新开始记录",
                "tone": ["真诚", "克制", "自然口语"],
                "must_include": ["重新开始"],
                "avoid_patterns": ["空泛排比", "强行金句"],
            },
        },
    )
    assert created.workflow_version == QUALITY_REVIEW_WORKFLOW_VERSION
    assert created.input_json["draft_quality"] == {
        "enabled": True,
        "profile": "podcast_draft_v1",
    }
    if force_legacy_v6:
        async with database.sessions() as session, session.begin():
            persisted = await session.get(Run, created.id)
            assert persisted is not None
            persisted.workflow_version = LEGACY_QUALITY_REVIEW_WORKFLOW_VERSION
    return created.id


async def _resume_with_enough_material(
    database: Database,
    service: RunService,
    worker: Worker,
    *,
    force_legacy_v6: bool = False,
) -> str:
    run_id = await _create_quality_run(
        database,
        service,
        force_legacy_v6=force_legacy_v6,
    )
    assert await worker.run_until_idle() == 3
    waiting = await service.get_run(run_id)
    assert waiting.status == "waiting_for_user"
    assert waiting.current_step == "awaiting_more_material"

    supplemental_source_id = await _import_material(
        database,
        title="质量工作流补充口述",
        text=_material("补充口述", paragraph_count=8, detail_count=10),
    )
    resumed = await service.resume_run(
        run_id,
        checkpoint="material_readiness",
        submission_id="quality-round-1",
        source_ids=[supplemental_source_id],
    )
    assert resumed.resumed is True
    assert resumed.run.status == "running"
    assert resumed.run.current_step == BUILD_PODCAST_DRAFT
    editors = [task for task in resumed.run.tasks if task.kind == BUILD_PODCAST_DRAFT]
    assert len(editors) == 1
    assert editors[0].status == "queued"
    return run_id


async def test_creative_brief_defaults_to_v7_and_explicit_opt_out_stays_v5(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, _worker = runtime
    source_id = await _import_material(
        database,
        title="版本选择素材",
        text=_material("版本", paragraph_count=2, detail_count=3),
    )
    default_quality = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "默认启用质量审阅",
            "source_ids": [source_id],
            "creative_brief": {"target_duration_minutes": 10},
        },
    )
    opted_out = await service.create_run(
        workflow_type="episode-research",
        payload={
            "topic": "显式关闭质量审阅",
            "source_ids": [source_id],
            "creative_brief": {"target_duration_minutes": 10},
            "draft_quality": {"enabled": False},
        },
    )

    assert default_quality.workflow_version == QUALITY_REVIEW_WORKFLOW_VERSION
    assert default_quality.input_json["draft_quality"]["enabled"] is True
    assert opted_out.workflow_version == "v5"
    assert opted_out.input_json["draft_quality"] == {
        "enabled": False,
        "profile": "podcast_draft_v1",
    }


async def test_v8_editor_queues_style_aware_reviewer_then_preserves_draft_as_final_output(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    run_id = await _resume_with_enough_material(database, service, worker)

    assert await worker.run_once() is True
    after_editor = await service.get_run(run_id)
    assert after_editor.status == "running"
    assert after_editor.current_step == REVIEW_PODCAST_DRAFT
    assert after_editor.model_call_count == 4
    reviewers = [task for task in after_editor.tasks if task.kind == REVIEW_PODCAST_DRAFT]
    assert len(reviewers) == 1
    assert reviewers[0].status == "queued"
    drafts = [
        artifact
        for artifact in after_editor.artifacts
        if artifact.kind == f"{BUILD_PODCAST_DRAFT}_result"
    ]
    metrics = [
        artifact for artifact in after_editor.artifacts if artifact.kind == "draft_metrics_report"
    ]
    assert len(drafts) == 1
    assert len(metrics) == 1
    persisted_deterministic = DeterministicDraftQualityResult.model_validate(
        metrics[0].content_json
    )
    assert persisted_deterministic.metrics.rules_version == DRAFT_QUALITY_RULES_VERSION
    async with database.sessions() as session:
        persisted_reviewer = await session.get(Task, reviewers[0].id)
    assert persisted_reviewer is not None
    assert (
        persisted_reviewer.input_json["review_contract_version"]
        == STYLE_AWARE_MODEL_REVIEW_TASK_VERSION
    )
    assert persisted_reviewer.input_json["deterministic_metrics_artifact_id"] == metrics[0].id
    assert persisted_reviewer.input_json["deterministic_quality_facts"] == (
        build_deterministic_quality_facts(persisted_deterministic).model_dump(mode="json")
    )
    assert after_editor.output_artifact_id == drafts[0].id
    assert all(artifact.kind != "draft_quality_report" for artifact in after_editor.artifacts)

    assert await worker.run_once() is True
    completed = await service.get_run(run_id)
    assert completed.status == "succeeded"
    assert completed.current_step == "complete"
    assert completed.output_artifact_id == drafts[0].id
    assert completed.model_call_count == 5
    assert len(completed.tasks) == 6
    assert len(completed.artifacts) == 11
    quality_artifact_kinds = [
        artifact.kind
        for artifact in completed.artifacts
        if artifact.kind
        in {
            "draft_metrics_report",
            f"{REVIEW_PODCAST_DRAFT}_result",
            "draft_quality_report",
        }
    ]
    assert sorted(quality_artifact_kinds) == sorted(
        [
            "draft_metrics_report",
            f"{REVIEW_PODCAST_DRAFT}_result",
            "draft_quality_report",
        ]
    )
    reviewer = next(task for task in completed.tasks if task.kind == REVIEW_PODCAST_DRAFT)
    assert reviewer.status == "succeeded"
    assert reviewer.attempt == 1
    report_record = await service.get_draft_quality_report(run_id)
    assert report_record.report.scoring_formula_version == STYLE_AWARE_DRAFT_QUALITY_FORMULA_VERSION
    assert report_record.report.writing_style_context_status == "not_provided"
    assert report_record.report.model_review_status == "completed"
    assert report_record.report.model_review_advisory is True
    assert report_record.report.requires_human_review is True
    assert report_record.report.reviewer_relation == "same_model"
    assert report_record.report.decision in {
        "blocked",
        "revision_recommended",
        "candidate_ready_for_human_review",
    }
    assert report_record.report.experimental_overall_score is not None
    assert "[S" in await service.export_podcast_draft_markdown(run_id)
    quality_markdown = await service.export_draft_quality_markdown(run_id)
    assert "口播稿质量报告" in quality_markdown
    assert "模型六个局部维度的简单平均" in quality_markdown
    assert "未校准加权综合分" in quality_markdown
    assert "代码拥有的非补偿式上限" in quality_markdown
    assert "校准后实验性综合分" in quality_markdown
    assert "证据位置：`podcast\\_script" in quality_markdown
    assert "src_" not in quality_markdown
    assert "seg_" not in quality_markdown
    quality_event = next(
        event
        for event in await service.list_events(run_id)
        if event.type == "workflow.draft_quality.completed"
    )
    assert quality_event.payload["experimental_uncapped_overall_score"] == (
        report_record.report.experimental_uncapped_overall_score
    )
    assert quality_event.payload["code_owned_score_cap"] == (
        report_record.report.code_owned_score_cap
    )
    assert quality_event.payload["model_review_conflict_count"] == len(
        report_record.report.model_review_conflicts
    )

    stable_counts = (
        len(completed.tasks),
        len(completed.artifacts),
        len(completed.model_calls),
        len(await service.list_events(run_id)),
    )
    assert await worker.run_until_idle() == 0
    unchanged = await service.get_run(run_id)
    assert (
        len(unchanged.tasks),
        len(unchanged.artifacts),
        len(unchanged.model_calls),
        len(await service.list_events(run_id)),
    ) == stable_counts


async def test_persisted_v6_reviewer_task_resumes_with_legacy_contract_after_restart(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    run_id = await _resume_with_enough_material(
        database,
        service,
        worker,
        force_legacy_v6=True,
    )

    assert await worker.run_once() is True
    after_editor = await service.get_run(run_id)
    assert after_editor.workflow_version == LEGACY_QUALITY_REVIEW_WORKFLOW_VERSION
    reviewer_view = next(task for task in after_editor.tasks if task.kind == REVIEW_PODCAST_DRAFT)
    async with database.sessions() as session:
        persisted_reviewer = await session.get(Task, reviewer_view.id)
    assert persisted_reviewer is not None
    assert "review_contract_version" not in persisted_reviewer.input_json
    assert "deterministic_metrics_artifact_id" not in persisted_reviewer.input_json
    assert "deterministic_quality_facts" not in persisted_reviewer.input_json
    legacy_metrics = next(
        artifact for artifact in after_editor.artifacts if artifact.kind == "draft_metrics_report"
    )
    parsed_legacy_metrics = DeterministicDraftQualityResult.model_validate(
        legacy_metrics.content_json
    )
    assert parsed_legacy_metrics.metrics.rules_version == LEGACY_DRAFT_QUALITY_RULES_VERSION
    assert parsed_legacy_metrics.metrics.chinese_style_heuristic_version is None
    legacy_prompt = build_quality_review_prompt(
        task_input=persisted_reviewer.input_json,
        max_bundle_chars=80_000,
    )
    assert legacy_prompt.version == LEGACY_QUALITY_REVIEW_PROMPT_VERSION
    assert "deterministic_quality_facts" not in "\n".join(
        message["content"] for message in legacy_prompt.messages
    )

    restarted_database = Database(str(database.engine.url))
    restarted_orchestrator = Orchestrator(task_max_attempts=2)
    restarted_worker = Worker(
        database=restarted_database,
        orchestrator=restarted_orchestrator,
        provider=FakeProvider(),
        lease_seconds=30,
        timeout_seconds=5,
        poll_interval_seconds=0.01,
    )
    restarted_service = RunService(restarted_database, restarted_orchestrator)
    try:
        assert await restarted_worker.run_once() is True
        completed = await restarted_service.get_run(run_id)
        assert completed.status == "succeeded"
        assert completed.workflow_version == LEGACY_QUALITY_REVIEW_WORKFLOW_VERSION
        report = (await restarted_service.get_draft_quality_report(run_id)).report
        assert report.scoring_formula_version == LEGACY_DRAFT_QUALITY_FORMULA_VERSION
        assert report.experimental_uncapped_overall_score is None
        assert report.code_owned_score_cap is None
        assert report.score_cap_reasons == []
        assert report.model_review_conflicts == []
        async with restarted_database.sessions() as session:
            report_artifact = next(
                artifact
                for artifact in (
                    await session.execute(
                        select(Artifact).where(
                            Artifact.run_id == run_id,
                            Artifact.kind == "draft_quality_report",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert report_artifact.idempotency_key.endswith(":v1")
        assert await restarted_worker.run_until_idle() == 0
    finally:
        await restarted_database.close()


async def test_prerelease_v6_current_reviewer_task_keeps_v2_caps_after_restart(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    """A v6 Run with persisted M3.5 facts must not be downgraded on recovery."""

    database, service, worker = runtime
    run_id = await _resume_with_enough_material(database, service, worker)
    assert await worker.run_once() is True

    async with database.sessions() as session, session.begin():
        persisted_run = await session.get(Run, run_id)
        assert persisted_run is not None
        persisted_run.workflow_version = LEGACY_QUALITY_REVIEW_WORKFLOW_VERSION
        reviewer_task = (
            await session.execute(
                select(Task).where(
                    Task.run_id == run_id,
                    Task.kind == REVIEW_PODCAST_DRAFT,
                )
            )
        ).scalar_one()
        prerelease_input = dict(reviewer_task.input_json)
        assert prerelease_input["review_contract_version"] == STYLE_AWARE_MODEL_REVIEW_TASK_VERSION
        prerelease_input.pop("review_contract_version")
        prerelease_input.pop("writing_style_profile", None)
        prerelease_input.pop("writing_style_segments", None)
        reviewer_task.input_json = prerelease_input
        metrics_artifact = await session.get(
            Artifact,
            prerelease_input["deterministic_metrics_artifact_id"],
        )
        assert metrics_artifact is not None
        assert metrics_artifact.idempotency_key.endswith(":v2")
        metrics_artifact.idempotency_key = (
            f"draft-metrics:{run_id}:{prerelease_input['draft_artifact_id']}:v1"
        )

    restarted_database = Database(str(database.engine.url))
    restarted_orchestrator = Orchestrator(task_max_attempts=2)
    restarted_worker = Worker(
        database=restarted_database,
        orchestrator=restarted_orchestrator,
        provider=FakeProvider(),
        lease_seconds=30,
        timeout_seconds=5,
        poll_interval_seconds=0.01,
    )
    restarted_service = RunService(restarted_database, restarted_orchestrator)
    try:
        assert await restarted_worker.run_once() is True
        completed = await restarted_service.get_run(run_id)
        assert completed.status == "succeeded"
        assert completed.workflow_version == LEGACY_QUALITY_REVIEW_WORKFLOW_VERSION
        report = (await restarted_service.get_draft_quality_report(run_id)).report
        assert report.scoring_formula_version == DRAFT_QUALITY_FORMULA_VERSION
        assert report.code_owned_score_cap == 39
        assert report.experimental_uncapped_overall_score is not None
        assert report.experimental_overall_score == 39
        assert report.model_review_conflicts
        async with restarted_database.sessions() as session:
            report_artifact = (
                await session.execute(
                    select(Artifact).where(
                        Artifact.run_id == run_id,
                        Artifact.kind == "draft_quality_report",
                    )
                )
            ).scalar_one()
        assert report_artifact.idempotency_key.endswith(":v2")
        assert await restarted_worker.run_until_idle() == 0
    finally:
        await restarted_database.close()


class TieredFakeDeepSeekProvider(FakeProvider):
    name = "deepseek"
    billing_currency = "USD"

    def __init__(self, model: str) -> None:
        self.model = model


async def test_v7_routes_only_the_trusted_reviewer_task_to_the_reviewer_tier(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    worker.provider = TieredFakeDeepSeekProvider("deepseek-v4-flash")
    worker.reviewer_provider = TieredFakeDeepSeekProvider("deepseek-v4-pro")
    run_id = await _resume_with_enough_material(database, service, worker)

    assert await worker.run_until_idle() == 2
    completed = await service.get_run(run_id)
    reviewer = next(task for task in completed.tasks if task.kind == REVIEW_PODCAST_DRAFT)
    reviewer_calls = [call for call in completed.model_calls if call.task_id == reviewer.id]
    non_reviewer_calls = [call for call in completed.model_calls if call.task_id != reviewer.id]

    assert len(reviewer_calls) == 1
    assert reviewer_calls[0].provider == "deepseek"
    assert reviewer_calls[0].model == "deepseek-v4-pro"
    assert non_reviewer_calls
    assert all(call.model == "deepseek-v4-flash" for call in non_reviewer_calls)

    reviewer_artifact = next(
        artifact
        for artifact in completed.artifacts
        if artifact.kind == f"{REVIEW_PODCAST_DRAFT}_result"
    )
    assert reviewer_artifact.content_json["_execution"]["model"] == "deepseek-v4-pro"
    report_record = await service.get_draft_quality_report(run_id)
    assert report_record.report.reviewer_relation == "cross_tier_same_family"


class FailReviewerOnceProvider(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        if invocation.kind == REVIEW_PODCAST_DRAFT and invocation.attempt == 1:
            raise RetryableProviderError("temporary Reviewer failure")
        return await super().generate(invocation)


async def test_v7_reviewer_transient_failure_retries_without_duplicate_artifacts(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    worker.provider = FailReviewerOnceProvider()
    run_id = await _resume_with_enough_material(database, service, worker)

    assert await worker.run_until_idle() == 3
    completed = await service.get_run(run_id)
    assert completed.status == "succeeded"
    assert completed.model_call_count == 6
    reviewer = next(task for task in completed.tasks if task.kind == REVIEW_PODCAST_DRAFT)
    assert reviewer.status == "succeeded"
    assert reviewer.attempt == 2
    reviewer_calls = [call for call in completed.model_calls if call.task_id == reviewer.id]
    assert [call.status for call in reviewer_calls] == ["failed", "succeeded"]
    assert [artifact.kind for artifact in completed.artifacts].count(
        f"{REVIEW_PODCAST_DRAFT}_result"
    ) == 1
    assert [artifact.kind for artifact in completed.artifacts].count("draft_quality_report") == 1
    events = await service.list_events(run_id)
    assert sum(event.type == "task.retry_scheduled" for event in events) == 1
    assert sum(event.type == "workflow.draft_self_review.completed" for event in events) == 1
    assert sum(event.type == "workflow.draft_quality.completed" for event in events) == 1


class PermanentlyFailingReviewerProvider(FakeProvider):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        if invocation.kind == REVIEW_PODCAST_DRAFT:
            raise ProviderAuthenticationError("Reviewer is unavailable")
        return await super().generate(invocation)


async def test_v7_permanent_reviewer_failure_degrades_but_keeps_draft_exportable(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    worker.provider = PermanentlyFailingReviewerProvider()
    run_id = await _resume_with_enough_material(database, service, worker)

    assert await worker.run_until_idle() == 2
    completed = await service.get_run(run_id)
    assert completed.status == "succeeded"
    assert completed.model_call_count == 5
    reviewer = next(task for task in completed.tasks if task.kind == REVIEW_PODCAST_DRAFT)
    assert reviewer.status == "failed"
    assert reviewer.error_code == "provider_authentication_failed"
    assert all(
        artifact.kind != f"{REVIEW_PODCAST_DRAFT}_result" for artifact in completed.artifacts
    )
    draft = next(
        artifact
        for artifact in completed.artifacts
        if artifact.kind == f"{BUILD_PODCAST_DRAFT}_result"
    )
    assert completed.output_artifact_id == draft.id
    report_record = await service.get_draft_quality_report(run_id)
    assert report_record.report.decision == "blocked"
    assert report_record.report.deterministic.has_blocker is True
    assert report_record.report.model_review_status == "unavailable"
    assert report_record.report.model_review_unavailable_reason == "provider_authentication_failed"
    assert report_record.report.experimental_model_score is None
    assert report_record.report.experimental_overall_score is None
    assert "[S" in await service.export_podcast_draft_markdown(run_id)
    events = await service.list_events(run_id)
    assert sum(event.type == "workflow.draft_self_review.unavailable" for event in events) == 1
    assert sum(event.type == "run.succeeded" for event in events) == 1
    assert all(event.type != "run.failed" for event in events)


async def test_v7_reviewer_budget_rejection_degrades_without_provider_call(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    worker.max_model_calls_per_run = 4
    run_id = await _resume_with_enough_material(database, service, worker)

    assert await worker.run_until_idle() == 2
    completed = await service.get_run(run_id)
    assert completed.status == "succeeded"
    assert completed.model_call_count == 4
    reviewer = next(task for task in completed.tasks if task.kind == REVIEW_PODCAST_DRAFT)
    assert reviewer.status == "failed"
    assert reviewer.error_code == "model_call_limit_exceeded"
    report = (await service.get_draft_quality_report(run_id)).report
    assert report.decision == "blocked"
    assert report.deterministic.has_blocker is True
    assert report.model_review_unavailable_reason == "model_call_limit_exceeded"
    assert completed.output_artifact_id is not None
    assert await service.export_podcast_draft_markdown(run_id)


async def test_v7_reviewer_lease_recovery_and_cancellation_are_durable(
    runtime: tuple[Database, RunService, Worker],
) -> None:
    database, service, worker = runtime
    recovered_run_id = await _resume_with_enough_material(database, service, worker)
    assert await worker.run_once() is True
    claimed = await worker.claim_next()
    assert claimed is not None
    assert claimed.kind == REVIEW_PODCAST_DRAFT
    async with database.sessions() as session, session.begin():
        reviewer = await session.get(Task, claimed.task_id)
        assert reviewer is not None
        reviewer.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert await worker.recover_expired() == 1
    assert await worker.run_once() is True
    recovered = await service.get_run(recovered_run_id)
    assert recovered.status == "succeeded"
    recovered_reviewer = next(task for task in recovered.tasks if task.kind == REVIEW_PODCAST_DRAFT)
    assert recovered_reviewer.status == "succeeded"
    assert recovered_reviewer.attempt == 2
    assert recovered.model_call_count == 5

    cancelled_run_id = await _resume_with_enough_material(database, service, worker)
    assert await worker.run_once() is True
    cancelled = await service.cancel_run(cancelled_run_id)
    assert cancelled.status == "cancelled"
    cancelled_reviewer = next(task for task in cancelled.tasks if task.kind == REVIEW_PODCAST_DRAFT)
    assert cancelled_reviewer.status == "cancelled"
    assert await worker.run_until_idle() == 0
    final_cancelled = await service.get_run(cancelled_run_id)
    assert final_cancelled.status == "cancelled"
    assert all(artifact.kind != "draft_quality_report" for artifact in final_cancelled.artifacts)
