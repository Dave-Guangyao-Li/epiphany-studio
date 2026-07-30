from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from epiphany.draft_quality import (
    analyze_podcast_draft,
    build_deterministic_quality_facts,
    build_draft_quality_report,
)
from epiphany.draft_quality_schemas import (
    DRAFT_QUALITY_FORMULA_VERSION,
    DRAFT_QUALITY_RULES_VERSION,
    LEGACY_DRAFT_QUALITY_FORMULA_VERSION,
    LEGACY_DRAFT_QUALITY_RULES_VERSION,
    LEGACY_MODEL_REVIEW_TASK_VERSION,
    MODEL_REVIEW_TASK_VERSION,
    REVIEW_PODCAST_DRAFT,
    DeterministicDraftQualityResult,
    ModelSelfReviewOutput,
    ModelSelfReviewTaskInput,
)
from epiphany.editor_schemas import BUILD_PODCAST_DRAFT, editor_output_reference_keys
from epiphany.events import append_event
from epiphany.interview_markdown import interview_scaffold_reference_keys
from epiphany.interview_schemas import (
    BUILD_INTERVIEW_SCAFFOLD,
    InterviewScaffoldOutput,
)
from epiphany.material_readiness import (
    MaterialReadinessReport,
    ReadinessFollowUpQuestion,
    assess_material_readiness,
)
from epiphany.models import Artifact, Run, Source, Task
from epiphany.research_schemas import THEME_RESEARCH, TIMELINE_RESEARCH
from epiphany.state_machine import (
    RunStatus,
    TaskStatus,
    validate_run_transition,
    validate_task_transition,
)

logger = logging.getLogger("epiphany.orchestrator")

FAKE_WORKFLOW_STEPS = ("prepare_sources", "fake_research", "assemble_artifact")
RESEARCH_MANAGER = "research_manager"
RESEARCH_CHILDREN = (TIMELINE_RESEARCH, THEME_RESEARCH)
LEGACY_RESEARCH_WORKFLOW_VERSION = "v1"
SCAFFOLD_RESEARCH_WORKFLOW_VERSION = "v2"
INTERVIEW_RESEARCH_WORKFLOW_VERSION = "v3"
EDITOR_RESEARCH_WORKFLOW_VERSION = "v4"
MATERIAL_READINESS_WORKFLOW_VERSION = "v5"
LEGACY_QUALITY_REVIEW_WORKFLOW_VERSION = "v6"
QUALITY_REVIEW_WORKFLOW_VERSION = "v7"
QUALITY_REVIEW_WORKFLOW_VERSIONS = (
    LEGACY_QUALITY_REVIEW_WORKFLOW_VERSION,
    QUALITY_REVIEW_WORKFLOW_VERSION,
)


class Orchestrator:
    def __init__(self, *, task_max_attempts: int) -> None:
        self.task_max_attempts = task_max_attempts

    async def start_run(
        self,
        session: AsyncSession,
        run: Run,
        *,
        research_source_segments: list[dict[str, str]] | None = None,
    ) -> list[Task]:
        if run.workflow_type == "fake-podcast":
            return [await self._enqueue_fake_initial_task(session, run)]
        if run.workflow_type == "episode-research":
            if not research_source_segments:
                raise ValueError("episode-research requires source segments")
            return await self._start_episode_research(
                session,
                run=run,
                source_segments=research_source_segments,
            )
        raise ValueError(f"unsupported workflow type: {run.workflow_type}")

    async def advance_after_success(
        self,
        session: AsyncSession,
        *,
        run: Run,
        completed_task: Task,
        artifact: Artifact,
    ) -> Task | None:
        if run.workflow_type == "fake-podcast":
            return await self._advance_fake_workflow(
                session,
                run=run,
                completed_task=completed_task,
                artifact=artifact,
            )
        if run.workflow_type == "episode-research":
            await self._advance_episode_research(
                session,
                run=run,
                completed_task=completed_task,
            )
            return None
        raise ValueError(f"unsupported workflow type: {run.workflow_type}")

    async def enqueue_editor(
        self,
        session: AsyncSession,
        *,
        run: Run,
        input_json: dict[str, Any],
    ) -> Task:
        """Queue the single Editor task after the durable human checkpoint."""

        if (
            run.workflow_type != "episode-research"
            or run.workflow_version
            not in {
                EDITOR_RESEARCH_WORKFLOW_VERSION,
                MATERIAL_READINESS_WORKFLOW_VERSION,
                *QUALITY_REVIEW_WORKFLOW_VERSIONS,
            }
            or run.status != RunStatus.RUNNING
        ):
            raise ValueError("editor can only be queued for a running v4/v5/v6/v7 episode workflow")

        task = await self._enqueue_task(
            session,
            run=run,
            kind=BUILD_PODCAST_DRAFT,
            agent_type="editor",
            parent_task_id=None,
            input_json=input_json,
        )
        await append_event(
            session,
            run_id=run.id,
            task_id=task.id,
            event_type="workflow.editor.queued",
            payload={
                "editor_task_id": task.id,
                "scaffold_artifact_id": input_json["scaffold_artifact_id"],
                "submission_artifact_id": input_json["submission_artifact_id"],
                "submission_artifact_count": len(input_json.get("submission_artifact_ids", []))
                or 1,
            },
        )
        logger.info(
            "Editor task queued",
            extra={
                "event": "workflow.editor.queued",
                "run_id": run.id,
                "task_id": task.id,
            },
        )
        return task

    async def fail_after_task(
        self,
        session: AsyncSession,
        *,
        run: Run,
        failed_task: Task,
    ) -> None:
        if (
            run.workflow_type == "episode-research"
            and run.workflow_version in QUALITY_REVIEW_WORKFLOW_VERSIONS
            and failed_task.kind == REVIEW_PODCAST_DRAFT
        ):
            await self._complete_quality_review_unavailable(
                session,
                run=run,
                failed_task=failed_task,
            )
            return

        if failed_task.parent_task_id is not None:
            parent = await session.get(Task, failed_task.parent_task_id)
            if parent is not None and parent.status == TaskStatus.RUNNING:
                validate_task_transition(parent.status, TaskStatus.FAILED)
                parent.status = TaskStatus.FAILED
                parent.error_code = "child_task_failed"
                parent.error_message = "a child research task failed"
                await append_event(
                    session,
                    run_id=run.id,
                    task_id=parent.id,
                    event_type="task.failed",
                    payload={
                        "kind": parent.kind,
                        "error_code": parent.error_code,
                        "failed_child_task_id": failed_task.id,
                    },
                )

            siblings = (
                await session.execute(
                    select(Task).where(
                        Task.parent_task_id == failed_task.parent_task_id,
                        Task.id != failed_task.id,
                        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
                    )
                )
            ).scalars()
            for sibling in siblings:
                validate_task_transition(sibling.status, TaskStatus.CANCELLED)
                sibling.status = TaskStatus.CANCELLED
                sibling.lease_token = None
                sibling.lease_expires_at = None
                await append_event(
                    session,
                    run_id=run.id,
                    task_id=sibling.id,
                    event_type="task.cancelled",
                    payload={
                        "kind": sibling.kind,
                        "reason": "sibling_failed",
                        "failed_task_id": failed_task.id,
                    },
                )

        validate_run_transition(run.status, RunStatus.FAILED)
        run.status = RunStatus.FAILED
        run.current_step = "failed"
        await append_event(
            session,
            run_id=run.id,
            event_type="run.failed",
            payload={"task_id": failed_task.id, "error_code": failed_task.error_code},
        )

    async def _enqueue_fake_initial_task(self, session: AsyncSession, run: Run) -> Task:
        return await self._enqueue_task(
            session,
            run=run,
            kind=FAKE_WORKFLOW_STEPS[0],
            agent_type="fake_agent",
            parent_task_id=None,
            input_json={
                "task_kind": FAKE_WORKFLOW_STEPS[0],
                "run_payload": run.input_json,
            },
        )

    async def _advance_fake_workflow(
        self,
        session: AsyncSession,
        *,
        run: Run,
        completed_task: Task,
        artifact: Artifact,
    ) -> Task | None:
        index = FAKE_WORKFLOW_STEPS.index(completed_task.kind)
        if index == len(FAKE_WORKFLOW_STEPS) - 1:
            validate_run_transition(run.status, RunStatus.SUCCEEDED)
            run.status = RunStatus.SUCCEEDED
            run.current_step = "complete"
            run.output_artifact_id = artifact.id
            await append_event(
                session,
                run_id=run.id,
                event_type="run.succeeded",
                payload={"output_artifact_id": artifact.id},
            )
            return None

        next_kind = FAKE_WORKFLOW_STEPS[index + 1]
        return await self._enqueue_task(
            session,
            run=run,
            kind=next_kind,
            agent_type="fake_agent",
            # Pipeline dependency is carried by the Artifact reference. parent_task_id
            # is reserved for the one-level subagent hierarchy introduced in M2.
            parent_task_id=None,
            input_json={
                "task_kind": next_kind,
                "run_payload": run.input_json,
                "previous_artifact_id": artifact.id,
                "previous_content": artifact.content_json,
            },
        )

    async def _start_episode_research(
        self,
        session: AsyncSession,
        *,
        run: Run,
        source_segments: list[dict[str, str]],
    ) -> list[Task]:
        validate_run_transition(run.status, RunStatus.RUNNING)
        run.status = RunStatus.RUNNING
        run.current_step = "research_fan_out"
        await append_event(session, run_id=run.id, event_type="run.started", payload={})

        manager = Task(
            run_id=run.id,
            parent_task_id=None,
            kind=RESEARCH_MANAGER,
            agent_type="workflow_manager",
            status=TaskStatus.RUNNING,
            attempt=1,
            max_attempts=1,
            input_json={
                "task_kind": RESEARCH_MANAGER,
                "source_ids": run.input_json["source_ids"],
            },
            idempotency_key=f"{run.id}:{RESEARCH_MANAGER}:{run.workflow_version}",
        )
        session.add(manager)
        await session.flush()
        await append_event(
            session,
            run_id=run.id,
            task_id=manager.id,
            event_type="task.started",
            payload={"kind": manager.kind, "attempt": manager.attempt},
        )
        await append_event(
            session,
            run_id=run.id,
            task_id=manager.id,
            event_type="workflow.fan_out.started",
            payload={"child_count": len(RESEARCH_CHILDREN)},
        )

        children = [
            await self._enqueue_task(
                session,
                run=run,
                kind=kind,
                agent_type=f"{kind}er",
                parent_task_id=manager.id,
                input_json={
                    "task_kind": kind,
                    "topic": run.input_json["topic"],
                    "source_segments": source_segments,
                },
            )
            for kind in RESEARCH_CHILDREN
        ]
        run.current_step = "research_parallel"
        logger.info(
            "Research workflow fanned out",
            extra={
                "event": "workflow.fan_out.started",
                "run_id": run.id,
                "task_id": manager.id,
                "child_count": len(children),
            },
        )
        return [manager, *children]

    async def _advance_episode_research(
        self,
        session: AsyncSession,
        *,
        run: Run,
        completed_task: Task,
    ) -> None:
        if completed_task.kind == REVIEW_PODCAST_DRAFT:
            await self._complete_draft_quality_review(
                session,
                run=run,
                completed_task=completed_task,
            )
            return
        if completed_task.kind == BUILD_PODCAST_DRAFT:
            await self._complete_podcast_draft(
                session,
                run=run,
                completed_task=completed_task,
            )
            return
        if completed_task.kind == BUILD_INTERVIEW_SCAFFOLD:
            await self._complete_interview_scaffold(
                session,
                run=run,
                completed_task=completed_task,
            )
            return
        if completed_task.kind not in RESEARCH_CHILDREN or completed_task.parent_task_id is None:
            raise ValueError(f"unexpected episode-research task: {completed_task.kind}")

        siblings = (
            (
                await session.execute(
                    select(Task)
                    .where(
                        Task.parent_task_id == completed_task.parent_task_id,
                        Task.kind.in_(RESEARCH_CHILDREN),
                    )
                    .order_by(Task.kind)
                )
            )
            .scalars()
            .all()
        )
        completed_count = sum(task.status == TaskStatus.SUCCEEDED for task in siblings)
        if completed_count != len(RESEARCH_CHILDREN):
            run.current_step = "research_fan_in_waiting"
            await append_event(
                session,
                run_id=run.id,
                task_id=completed_task.parent_task_id,
                event_type="workflow.fan_in.waiting",
                payload={
                    "completed_count": completed_count,
                    "remaining_count": len(RESEARCH_CHILDREN) - completed_count,
                },
            )
            return

        by_kind = {task.kind: task for task in siblings}
        if set(by_kind) != set(RESEARCH_CHILDREN):
            raise ValueError("research fan-in received an unexpected child task set")

        artifacts = (
            (
                await session.execute(
                    select(Artifact).where(
                        Artifact.id.in_([task.output_artifact_id for task in siblings])
                    )
                )
            )
            .scalars()
            .all()
        )
        artifacts_by_task_id = {artifact.task_id: artifact for artifact in artifacts}
        if len(artifacts_by_task_id) != len(RESEARCH_CHILDREN):
            raise ValueError("research fan-in could not load all child artifacts")

        bundle_key = f"research-bundle:{run.id}:{run.workflow_version}"
        bundle = (
            await session.execute(select(Artifact).where(Artifact.idempotency_key == bundle_key))
        ).scalar_one_or_none()
        if bundle is None:
            bundle = Artifact(
                run_id=run.id,
                task_id=completed_task.parent_task_id,
                kind="episode_research_bundle",
                content_json={
                    kind: {
                        "artifact_id": artifacts_by_task_id[by_kind[kind].id].id,
                        "content": artifacts_by_task_id[by_kind[kind].id].content_json,
                    }
                    for kind in RESEARCH_CHILDREN
                },
                idempotency_key=bundle_key,
            )
            session.add(bundle)
            await session.flush()

        manager = await session.get(Task, completed_task.parent_task_id)
        if manager is None:
            raise ValueError("research manager task is missing")
        validate_task_transition(manager.status, TaskStatus.SUCCEEDED)
        manager.status = TaskStatus.SUCCEEDED
        manager.output_artifact_id = bundle.id

        await append_event(
            session,
            run_id=run.id,
            task_id=manager.id,
            event_type="workflow.fan_in.completed",
            payload={
                "child_count": len(RESEARCH_CHILDREN),
                "artifact_id": bundle.id,
            },
        )
        await append_event(
            session,
            run_id=run.id,
            task_id=manager.id,
            event_type="task.succeeded",
            payload={
                "kind": manager.kind,
                "attempt": manager.attempt,
                "artifact_id": bundle.id,
            },
        )
        if run.workflow_version == LEGACY_RESEARCH_WORKFLOW_VERSION:
            validate_run_transition(run.status, RunStatus.SUCCEEDED)
            run.status = RunStatus.SUCCEEDED
            run.current_step = "complete"
            run.output_artifact_id = bundle.id
            await append_event(
                session,
                run_id=run.id,
                event_type="run.succeeded",
                payload={"output_artifact_id": bundle.id},
            )
            logger.info(
                "Legacy research workflow completed at fan-in",
                extra={
                    "event": "workflow.fan_in.completed",
                    "run_id": run.id,
                    "task_id": manager.id,
                    "artifact_id": bundle.id,
                    "child_count": len(RESEARCH_CHILDREN),
                },
            )
            return
        if run.workflow_version not in {
            SCAFFOLD_RESEARCH_WORKFLOW_VERSION,
            INTERVIEW_RESEARCH_WORKFLOW_VERSION,
            EDITOR_RESEARCH_WORKFLOW_VERSION,
            MATERIAL_READINESS_WORKFLOW_VERSION,
            *QUALITY_REVIEW_WORKFLOW_VERSIONS,
        }:
            raise ValueError(
                f"unsupported episode-research workflow version: {run.workflow_version}"
            )

        scaffold_task = await self._enqueue_task(
            session,
            run=run,
            kind=BUILD_INTERVIEW_SCAFFOLD,
            agent_type="interviewer",
            parent_task_id=None,
            input_json={
                "task_kind": BUILD_INTERVIEW_SCAFFOLD,
                "topic": run.input_json["topic"],
                "research_bundle_artifact_id": bundle.id,
                "timeline": _without_execution(
                    artifacts_by_task_id[by_kind[TIMELINE_RESEARCH].id].content_json
                ),
                "themes": _without_execution(
                    artifacts_by_task_id[by_kind[THEME_RESEARCH].id].content_json
                ),
            },
        )
        await append_event(
            session,
            run_id=run.id,
            task_id=scaffold_task.id,
            event_type="workflow.interview_scaffold.queued",
            payload={
                "research_bundle_artifact_id": bundle.id,
                "scaffold_task_id": scaffold_task.id,
            },
        )
        logger.info(
            "Research workflow fanned in",
            extra={
                "event": "workflow.fan_in.completed",
                "run_id": run.id,
                "task_id": manager.id,
                "artifact_id": bundle.id,
                "child_count": len(RESEARCH_CHILDREN),
            },
        )
        logger.info(
            "Interview scaffold task queued",
            extra={
                "event": "workflow.interview_scaffold.queued",
                "run_id": run.id,
                "task_id": scaffold_task.id,
                "artifact_id": bundle.id,
            },
        )

    async def _complete_interview_scaffold(
        self,
        session: AsyncSession,
        *,
        run: Run,
        completed_task: Task,
    ) -> None:
        if completed_task.parent_task_id is not None:
            raise ValueError("interview scaffold task must be a sequential root task")
        if completed_task.output_artifact_id is None:
            raise ValueError("interview scaffold task has no output artifact")

        artifact = await session.get(Artifact, completed_task.output_artifact_id)
        if artifact is None:
            raise ValueError("interview scaffold artifact is missing")
        sections = artifact.content_json.get("sections", [])
        question_count = sum(len(section.get("questions", [])) for section in sections)

        await append_event(
            session,
            run_id=run.id,
            task_id=completed_task.id,
            event_type="workflow.interview_scaffold.completed",
            payload={
                "artifact_id": artifact.id,
                "section_count": len(sections),
                "question_count": question_count,
            },
        )
        run.output_artifact_id = artifact.id
        if run.workflow_version == SCAFFOLD_RESEARCH_WORKFLOW_VERSION:
            validate_run_transition(run.status, RunStatus.SUCCEEDED)
            run.status = RunStatus.SUCCEEDED
            run.current_step = "complete"
            await append_event(
                session,
                run_id=run.id,
                event_type="run.succeeded",
                payload={"output_artifact_id": artifact.id},
            )
        elif run.workflow_version in {
            INTERVIEW_RESEARCH_WORKFLOW_VERSION,
            EDITOR_RESEARCH_WORKFLOW_VERSION,
        }:
            validate_run_transition(run.status, RunStatus.WAITING_FOR_USER)
            run.status = RunStatus.WAITING_FOR_USER
            run.current_step = "awaiting_interview_response"
            await append_event(
                session,
                run_id=run.id,
                event_type="workflow.user_input.requested",
                payload={
                    "checkpoint": "interview_scaffold",
                    "output_artifact_id": artifact.id,
                },
            )
            await append_event(
                session,
                run_id=run.id,
                event_type="run.waiting_for_user",
                payload={
                    "checkpoint": "interview_scaffold",
                    "output_artifact_id": artifact.id,
                },
            )
            logger.info(
                "Run waiting for user input",
                extra={
                    "event": "run.waiting_for_user",
                    "run_id": run.id,
                    "task_id": completed_task.id,
                    "artifact_id": artifact.id,
                    "checkpoint": "interview_scaffold",
                    "section_count": len(sections),
                    "question_count": question_count,
                },
            )
        elif run.workflow_version in {
            MATERIAL_READINESS_WORKFLOW_VERSION,
            *QUALITY_REVIEW_WORKFLOW_VERSIONS,
        }:
            await self._pause_for_material_readiness(
                session,
                run=run,
                completed_task=completed_task,
                scaffold=artifact,
            )
        else:
            raise ValueError(
                f"unsupported episode-research workflow version: {run.workflow_version}"
            )
        logger.info(
            "Interview scaffold completed",
            extra={
                "event": "workflow.interview_scaffold.completed",
                "run_id": run.id,
                "task_id": completed_task.id,
                "artifact_id": artifact.id,
                "section_count": len(sections),
                "question_count": question_count,
            },
        )

    async def _complete_podcast_draft(
        self,
        session: AsyncSession,
        *,
        run: Run,
        completed_task: Task,
    ) -> None:
        if run.workflow_version not in {
            EDITOR_RESEARCH_WORKFLOW_VERSION,
            MATERIAL_READINESS_WORKFLOW_VERSION,
            *QUALITY_REVIEW_WORKFLOW_VERSIONS,
        }:
            raise ValueError("editor task is only supported by the v4/v5/v6/v7 episode workflow")
        if completed_task.parent_task_id is not None:
            raise ValueError("editor task must be a sequential root task")
        if completed_task.output_artifact_id is None:
            raise ValueError("editor task has no output artifact")

        artifact = await session.get(Artifact, completed_task.output_artifact_id)
        if (
            artifact is None
            or artifact.run_id != run.id
            or artifact.kind != f"{BUILD_PODCAST_DRAFT}_result"
        ):
            raise ValueError("editor output artifact is missing or invalid")

        await append_event(
            session,
            run_id=run.id,
            task_id=completed_task.id,
            event_type="workflow.editor.completed",
            payload={"artifact_id": artifact.id},
        )
        logger.info(
            "Editor workflow step completed",
            extra={
                "event": "workflow.editor.completed",
                "run_id": run.id,
                "task_id": completed_task.id,
                "artifact_id": artifact.id,
            },
        )
        run.output_artifact_id = artifact.id
        if run.workflow_version in QUALITY_REVIEW_WORKFLOW_VERSIONS:
            await self._enqueue_draft_quality_review(
                session,
                run=run,
                editor_task=completed_task,
                draft_artifact=artifact,
            )
            return

        validate_run_transition(run.status, RunStatus.SUCCEEDED)
        run.status = RunStatus.SUCCEEDED
        run.current_step = "complete"
        await append_event(
            session,
            run_id=run.id,
            event_type="run.succeeded",
            payload={"output_artifact_id": artifact.id},
        )

    async def _enqueue_draft_quality_review(
        self,
        session: AsyncSession,
        *,
        run: Run,
        editor_task: Task,
        draft_artifact: Artifact,
    ) -> None:
        """Persist code-owned metrics before scheduling one advisory Reviewer."""

        draft_content = _without_execution(draft_artifact.content_json)
        deterministic = analyze_podcast_draft(
            draft=draft_content,
            creative_brief=run.input_json["creative_brief"],
            config=run.input_json["draft_quality"],
            rules_version=(
                LEGACY_DRAFT_QUALITY_RULES_VERSION
                if run.workflow_version == LEGACY_QUALITY_REVIEW_WORKFLOW_VERSION
                else DRAFT_QUALITY_RULES_VERSION
            ),
        )
        metrics_key_suffix = (
            "v1" if run.workflow_version == LEGACY_QUALITY_REVIEW_WORKFLOW_VERSION else "v2"
        )
        metrics_key = f"draft-metrics:{run.id}:{draft_artifact.id}:{metrics_key_suffix}"
        metrics_artifact = (
            await session.execute(select(Artifact).where(Artifact.idempotency_key == metrics_key))
        ).scalar_one_or_none()
        if metrics_artifact is None:
            metrics_artifact = Artifact(
                run_id=run.id,
                task_id=editor_task.id,
                kind="draft_metrics_report",
                content_json=deterministic.model_dump(mode="json"),
                idempotency_key=metrics_key,
            )
            session.add(metrics_artifact)
            await session.flush()
            await append_event(
                session,
                run_id=run.id,
                task_id=editor_task.id,
                event_type="workflow.draft_metrics.evaluated",
                payload={
                    "draft_artifact_id": draft_artifact.id,
                    "metrics_artifact_id": metrics_artifact.id,
                    "deterministic_score": deterministic.deterministic_score,
                    "hard_blocker_count": sum(
                        finding.status == "blocker" for finding in deterministic.findings
                    ),
                    "warning_count": sum(
                        finding.status == "warning" for finding in deterministic.findings
                    ),
                    "estimated_duration_minutes": (
                        deterministic.metrics.estimated_duration_minutes
                    ),
                },
            )
            logger.info(
                "Draft deterministic metrics evaluated",
                extra={
                    "event": "workflow.draft_metrics.evaluated",
                    "run_id": run.id,
                    "task_id": editor_task.id,
                    "artifact_id": metrics_artifact.id,
                    "draft_artifact_id": draft_artifact.id,
                    "deterministic_score": deterministic.deterministic_score,
                    "estimated_duration_minutes": (
                        deterministic.metrics.estimated_duration_minutes
                    ),
                },
            )
        deterministic = DeterministicDraftQualityResult.model_validate(
            metrics_artifact.content_json
        )

        allowed_keys = set(editor_output_reference_keys(draft_content))
        segment_candidates = [
            *editor_task.input_json.get("initial_source_segments", []),
            *editor_task.input_json.get("supplemental_source_segments", []),
        ]
        segments_by_key = {
            (
                str(segment["source_id"]),
                str(segment["source_segment_id"]),
            ): segment
            for segment in segment_candidates
        }
        missing_keys = allowed_keys - set(segments_by_key)
        if missing_keys:
            raise ValueError("draft quality review source material is unavailable")
        sorted_keys = sorted(allowed_keys)
        review_payload: dict[str, Any] = {
            "task_kind": REVIEW_PODCAST_DRAFT,
            "draft_artifact_id": draft_artifact.id,
            "creative_brief": run.input_json["creative_brief"],
            "quality_config": run.input_json["draft_quality"],
            "podcast_draft": draft_content,
            "allowed_source_refs": [
                {
                    "source_id": source_id,
                    "source_segment_id": segment_id,
                }
                for source_id, segment_id in sorted_keys
            ],
            "referenced_source_segments": [
                {
                    "source_id": source_id,
                    "source_segment_id": segment_id,
                    "text": str(segments_by_key[(source_id, segment_id)]["text"]),
                }
                for source_id, segment_id in sorted_keys
            ],
        }
        if run.workflow_version == QUALITY_REVIEW_WORKFLOW_VERSION:
            review_payload.update(
                {
                    "review_contract_version": MODEL_REVIEW_TASK_VERSION,
                    "deterministic_metrics_artifact_id": metrics_artifact.id,
                    "deterministic_quality_facts": build_deterministic_quality_facts(
                        deterministic
                    ).model_dump(mode="json"),
                }
            )
        else:
            review_payload["review_contract_version"] = LEGACY_MODEL_REVIEW_TASK_VERSION

        parsed_review_input = ModelSelfReviewTaskInput.model_validate(review_payload)
        review_input = parsed_review_input.model_dump(
            mode="json",
            exclude=(
                {
                    "review_contract_version",
                    "deterministic_metrics_artifact_id",
                    "deterministic_quality_facts",
                }
                if run.workflow_version == LEGACY_QUALITY_REVIEW_WORKFLOW_VERSION
                else None
            ),
        )
        review_task = await self._enqueue_task(
            session,
            run=run,
            kind=REVIEW_PODCAST_DRAFT,
            agent_type="quality_reviewer",
            parent_task_id=None,
            input_json=review_input,
        )
        await append_event(
            session,
            run_id=run.id,
            task_id=review_task.id,
            event_type="workflow.draft_self_review.queued",
            payload={
                "draft_artifact_id": draft_artifact.id,
                "metrics_artifact_id": metrics_artifact.id,
                "review_task_id": review_task.id,
                "source_segment_count": len(sorted_keys),
            },
        )
        logger.info(
            "Draft quality self-review queued",
            extra={
                "event": "workflow.draft_self_review.queued",
                "run_id": run.id,
                "task_id": review_task.id,
                "draft_artifact_id": draft_artifact.id,
                "metrics_artifact_id": metrics_artifact.id,
                "hard_blocker_count": sum(
                    finding.status == "blocker" for finding in deterministic.findings
                ),
                "warning_count": sum(
                    finding.status == "warning" for finding in deterministic.findings
                ),
            },
        )

    async def _complete_draft_quality_review(
        self,
        session: AsyncSession,
        *,
        run: Run,
        completed_task: Task,
    ) -> None:
        if (
            run.workflow_version not in QUALITY_REVIEW_WORKFLOW_VERSIONS
            or completed_task.parent_task_id is not None
            or completed_task.output_artifact_id is None
        ):
            raise ValueError("quality Reviewer is only supported by a sequential v6/v7 workflow")

        review_artifact = await session.get(Artifact, completed_task.output_artifact_id)
        if (
            review_artifact is None
            or review_artifact.run_id != run.id
            or review_artifact.kind != f"{REVIEW_PODCAST_DRAFT}_result"
        ):
            raise ValueError("quality Reviewer output artifact is missing or invalid")
        draft_artifact_id = str(completed_task.input_json["draft_artifact_id"])
        draft_artifact = await session.get(Artifact, draft_artifact_id)
        if (
            draft_artifact is None
            or draft_artifact.run_id != run.id
            or draft_artifact.kind != f"{BUILD_PODCAST_DRAFT}_result"
        ):
            raise ValueError("quality Reviewer draft artifact is missing or invalid")
        deterministic, review_contract_version = await self._load_deterministic_quality(
            session,
            run=run,
            reviewer_task=completed_task,
            draft_artifact_id=draft_artifact.id,
        )
        review = ModelSelfReviewOutput.model_validate(
            _without_execution(review_artifact.content_json)
        )
        report_artifact = await self._persist_draft_quality_report(
            session,
            run=run,
            draft_artifact=draft_artifact,
            deterministic=deterministic,
            model_self_review=review,
            reviewer_artifact=review_artifact,
            reviewer_task_id=completed_task.id,
            review_contract_version=review_contract_version,
        )
        await append_event(
            session,
            run_id=run.id,
            task_id=completed_task.id,
            event_type="workflow.draft_self_review.completed",
            payload={
                "draft_artifact_id": draft_artifact.id,
                "review_artifact_id": review_artifact.id,
                "quality_report_id": report_artifact.id,
            },
        )
        logger.info(
            "Draft quality self-review completed",
            extra={
                "event": "workflow.draft_self_review.completed",
                "run_id": run.id,
                "task_id": completed_task.id,
                "artifact_id": review_artifact.id,
                "draft_artifact_id": draft_artifact.id,
                "quality_report_id": report_artifact.id,
            },
        )
        await self._succeed_after_quality_review(
            session,
            run=run,
            draft_artifact=draft_artifact,
            report_artifact=report_artifact,
        )

    async def _complete_quality_review_unavailable(
        self,
        session: AsyncSession,
        *,
        run: Run,
        failed_task: Task,
    ) -> None:
        """Degrade safely: a Reviewer failure must not hide a valid Draft."""

        draft_artifact_id = str(failed_task.input_json.get("draft_artifact_id", ""))
        draft_artifact = await session.get(Artifact, draft_artifact_id)
        if (
            draft_artifact is None
            or draft_artifact.run_id != run.id
            or draft_artifact.kind != f"{BUILD_PODCAST_DRAFT}_result"
        ):
            raise ValueError("failed quality Reviewer has no valid Draft to preserve")
        deterministic, review_contract_version = await self._load_deterministic_quality(
            session,
            run=run,
            reviewer_task=failed_task,
            draft_artifact_id=draft_artifact.id,
        )
        report_artifact = await self._persist_draft_quality_report(
            session,
            run=run,
            draft_artifact=draft_artifact,
            deterministic=deterministic,
            model_self_review=None,
            reviewer_artifact=None,
            reviewer_task_id=failed_task.id,
            unavailable_reason=failed_task.error_code or "model_self_review_unavailable",
            review_contract_version=review_contract_version,
        )
        await append_event(
            session,
            run_id=run.id,
            task_id=failed_task.id,
            event_type="workflow.draft_self_review.unavailable",
            payload={
                "draft_artifact_id": draft_artifact.id,
                "quality_report_id": report_artifact.id,
                "error_code": failed_task.error_code,
            },
        )
        await self._succeed_after_quality_review(
            session,
            run=run,
            draft_artifact=draft_artifact,
            report_artifact=report_artifact,
        )

    async def _load_deterministic_quality(
        self,
        session: AsyncSession,
        *,
        run: Run,
        reviewer_task: Task,
        draft_artifact_id: str,
    ) -> tuple[DeterministicDraftQualityResult, str]:
        """Load metrics using the persisted Reviewer contract, not only the Run label.

        An M3.5 pre-release process could persist a workflow-v6 Run whose
        Reviewer Task already contained the current deterministic-facts
        contract. The Task payload is the durable execution instruction, so a
        restart must not silently downgrade it to the M3.4 formula.
        """

        review_input = ModelSelfReviewTaskInput.model_validate(reviewer_task.input_json)
        if review_input.draft_artifact_id != draft_artifact_id:
            raise ValueError("quality Reviewer task references a different Draft")

        if review_input.review_contract_version == MODEL_REVIEW_TASK_VERSION:
            metrics_artifact = await session.get(
                Artifact,
                review_input.deterministic_metrics_artifact_id,
            )
        else:
            metrics_artifact = (
                await session.execute(
                    select(Artifact).where(
                        Artifact.idempotency_key == f"draft-metrics:{run.id}:{draft_artifact_id}:v1"
                    )
                )
            ).scalar_one_or_none()
        if (
            metrics_artifact is None
            or metrics_artifact.run_id != run.id
            or metrics_artifact.kind != "draft_metrics_report"
        ):
            raise ValueError("deterministic Draft metrics are missing")
        deterministic = DeterministicDraftQualityResult.model_validate(
            metrics_artifact.content_json
        )
        if review_input.review_contract_version == MODEL_REVIEW_TASK_VERSION:
            if (
                deterministic.metrics.rules_version != DRAFT_QUALITY_RULES_VERSION
                or build_deterministic_quality_facts(deterministic)
                != review_input.deterministic_quality_facts
            ):
                raise ValueError(
                    "current quality Reviewer facts differ from persisted Draft metrics"
                )
        elif deterministic.metrics.rules_version != LEGACY_DRAFT_QUALITY_RULES_VERSION:
            raise ValueError("legacy quality Reviewer must use legacy Draft metrics")
        return deterministic, review_input.review_contract_version

    async def _persist_draft_quality_report(
        self,
        session: AsyncSession,
        *,
        run: Run,
        draft_artifact: Artifact,
        deterministic: DeterministicDraftQualityResult,
        model_self_review: ModelSelfReviewOutput | None,
        reviewer_artifact: Artifact | None,
        reviewer_task_id: str,
        unavailable_reason: str | None = None,
        review_contract_version: str,
    ) -> Artifact:
        scoring_formula_version = (
            LEGACY_DRAFT_QUALITY_FORMULA_VERSION
            if review_contract_version == LEGACY_MODEL_REVIEW_TASK_VERSION
            else DRAFT_QUALITY_FORMULA_VERSION
        )
        report_key_suffix = (
            "v1" if scoring_formula_version == LEGACY_DRAFT_QUALITY_FORMULA_VERSION else "v2"
        )
        report_key = f"draft-quality:{run.id}:{draft_artifact.id}:{report_key_suffix}"
        existing = (
            await session.execute(select(Artifact).where(Artifact.idempotency_key == report_key))
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        editor_execution = draft_artifact.content_json.get("_execution", {})
        reviewer_execution = (
            reviewer_artifact.content_json.get("_execution", {})
            if reviewer_artifact is not None
            else {}
        )
        report = build_draft_quality_report(
            deterministic=deterministic,
            model_self_review=model_self_review,
            editor_provider=editor_execution.get("provider"),
            editor_model=editor_execution.get("model"),
            reviewer_provider=reviewer_execution.get("provider"),
            reviewer_model=reviewer_execution.get("model"),
            unavailable_reason=unavailable_reason,
            scoring_formula_version=scoring_formula_version,
        )
        artifact = Artifact(
            run_id=run.id,
            task_id=reviewer_task_id,
            kind="draft_quality_report",
            content_json=report.model_dump(mode="json"),
            idempotency_key=report_key,
        )
        session.add(artifact)
        await session.flush()
        await append_event(
            session,
            run_id=run.id,
            task_id=reviewer_task_id,
            event_type="workflow.draft_quality.completed",
            payload={
                "draft_artifact_id": draft_artifact.id,
                "quality_report_id": artifact.id,
                "quality_decision": report.decision,
                "experimental_uncapped_overall_score": (report.experimental_uncapped_overall_score),
                "experimental_overall_score": report.experimental_overall_score,
                "code_owned_score_cap": report.code_owned_score_cap,
                "model_review_conflict_count": len(report.model_review_conflicts),
                "hard_blocker_count": sum(
                    finding.status == "blocker" for finding in deterministic.findings
                ),
                "warning_count": sum(
                    finding.status == "warning" for finding in deterministic.findings
                ),
                "requires_human_review": report.requires_human_review,
            },
        )
        return artifact

    async def _succeed_after_quality_review(
        self,
        session: AsyncSession,
        *,
        run: Run,
        draft_artifact: Artifact,
        report_artifact: Artifact,
    ) -> None:
        validate_run_transition(run.status, RunStatus.SUCCEEDED)
        run.status = RunStatus.SUCCEEDED
        run.current_step = "complete"
        run.output_artifact_id = draft_artifact.id
        await append_event(
            session,
            run_id=run.id,
            event_type="run.succeeded",
            payload={
                "output_artifact_id": draft_artifact.id,
                "quality_report_id": report_artifact.id,
            },
        )
        logger.info(
            "Draft quality workflow completed",
            extra={
                "event": "workflow.draft_quality.completed",
                "run_id": run.id,
                "artifact_id": draft_artifact.id,
                "quality_report_id": report_artifact.id,
                "quality_decision": report_artifact.content_json["decision"],
                "experimental_uncapped_overall_score": report_artifact.content_json.get(
                    "experimental_uncapped_overall_score"
                ),
                "experimental_overall_score": report_artifact.content_json.get(
                    "experimental_overall_score"
                ),
                "code_owned_score_cap": report_artifact.content_json.get("code_owned_score_cap"),
                "model_review_conflict_count": len(
                    report_artifact.content_json.get("model_review_conflicts", [])
                ),
            },
        )

    async def persist_material_readiness(
        self,
        session: AsyncSession,
        *,
        run: Run,
        report: MaterialReadinessReport,
        round_key: str,
        task_id: str | None,
    ) -> Artifact:
        """Commit a deterministic, text-free readiness report exactly once."""

        idempotency_key = f"material-readiness:{run.id}:{run.workflow_version}:{round_key}"
        existing = (
            await session.execute(
                select(Artifact).where(Artifact.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        artifact = Artifact(
            run_id=run.id,
            task_id=task_id,
            kind="material_readiness_report",
            content_json=report.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        session.add(artifact)
        await session.flush()
        await append_event(
            session,
            run_id=run.id,
            task_id=task_id,
            event_type="workflow.material_readiness.evaluated",
            payload={
                "artifact_id": artifact.id,
                "status": report.status,
                "target_duration_minutes": report.target_duration_minutes,
                "available_source_char_count": (report.counts.available_source_char_count),
                "additional_source_chars_needed": (report.additional_source_chars_needed),
                "source_count": (
                    report.counts.initial_source_count + report.counts.supplemental_source_count
                ),
                "segment_count": (
                    report.counts.initial_segment_count + report.counts.supplemental_segment_count
                ),
                "method": report.method,
            },
        )
        logger.info(
            "Material readiness evaluated",
            extra={
                "event": "workflow.material_readiness.evaluated",
                "run_id": run.id,
                "task_id": task_id,
                "artifact_id": artifact.id,
                "readiness_status": report.status,
                "target_duration_minutes": report.target_duration_minutes,
                "available_source_char_count": (report.counts.available_source_char_count),
                "additional_source_chars_needed": (report.additional_source_chars_needed),
            },
        )
        return artifact

    async def _pause_for_material_readiness(
        self,
        session: AsyncSession,
        *,
        run: Run,
        completed_task: Task,
        scaffold: Artifact,
    ) -> None:
        try:
            parsed_scaffold = InterviewScaffoldOutput.model_validate(
                _without_execution(scaffold.content_json)
            )
        except (ValueError, TypeError) as error:
            raise ValueError("interview scaffold cannot seed material readiness") from error

        sources = (
            (
                await session.execute(
                    select(Source)
                    .where(Source.id.in_(run.input_json["source_ids"]))
                    .options(selectinload(Source.segments))
                )
            )
            .scalars()
            .all()
        )
        segments_by_key = {
            (source.id, segment.id): segment for source in sources for segment in source.segments
        }
        reference_keys = interview_scaffold_reference_keys(
            _without_execution(scaffold.content_json)
        )
        missing_reference_keys = [key for key in reference_keys if key not in segments_by_key]
        if missing_reference_keys:
            raise ValueError("interview scaffold references unavailable initial source material")
        initial_segments = [
            {
                "source_id": source_id,
                "source_segment_id": segment_id,
                "text": segments_by_key[(source_id, segment_id)].text,
            }
            for source_id, segment_id in reference_keys
        ]
        follow_up_questions = [
            ReadinessFollowUpQuestion(
                prompt=question.prompt,
                purpose=question.purpose,
                source_refs=question.source_refs,
            )
            for section in parsed_scaffold.sections
            for question in section.questions
        ][:6]
        report = assess_material_readiness(
            creative_brief=run.input_json["creative_brief"],
            initial_source_segments=initial_segments,
            supplemental_source_segments=[],
            follow_up_questions=follow_up_questions,
        )
        readiness_artifact = await self.persist_material_readiness(
            session,
            run=run,
            report=report,
            round_key="initial",
            task_id=completed_task.id,
        )

        validate_run_transition(run.status, RunStatus.WAITING_FOR_USER)
        run.status = RunStatus.WAITING_FOR_USER
        run.current_step = "awaiting_more_material"
        # The scaffold remains the human-facing checkpoint output. Readiness is
        # independently addressable through the Artifact list and export API.
        run.output_artifact_id = scaffold.id
        checkpoint_payload = {
            "checkpoint": "material_readiness",
            "output_artifact_id": scaffold.id,
            "readiness_artifact_id": readiness_artifact.id,
            "readiness_status": report.status,
            "additional_source_chars_needed": report.additional_source_chars_needed,
        }
        await append_event(
            session,
            run_id=run.id,
            event_type="workflow.user_input.requested",
            payload=checkpoint_payload,
        )
        await append_event(
            session,
            run_id=run.id,
            event_type="run.waiting_for_user",
            payload=checkpoint_payload,
        )
        logger.info(
            "Run waiting for supplemental material",
            extra={
                "event": "run.waiting_for_user",
                "run_id": run.id,
                "task_id": completed_task.id,
                "artifact_id": scaffold.id,
                "readiness_artifact_id": readiness_artifact.id,
                "checkpoint": "material_readiness",
                "readiness_status": report.status,
                "additional_source_chars_needed": (report.additional_source_chars_needed),
            },
        )

    async def _enqueue_task(
        self,
        session: AsyncSession,
        *,
        run: Run,
        kind: str,
        agent_type: str,
        parent_task_id: str | None,
        input_json: dict[str, Any],
    ) -> Task:
        idempotency_key = f"{run.id}:{kind}:{run.workflow_version}"
        existing = (
            await session.execute(select(Task).where(Task.idempotency_key == idempotency_key))
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        task = Task(
            run_id=run.id,
            parent_task_id=parent_task_id,
            kind=kind,
            agent_type=agent_type,
            status=TaskStatus.QUEUED,
            max_attempts=self.task_max_attempts,
            input_json=input_json,
            idempotency_key=idempotency_key,
        )
        session.add(task)
        await session.flush()
        run.current_step = kind
        await append_event(
            session,
            run_id=run.id,
            task_id=task.id,
            event_type="task.queued",
            payload={"kind": kind, "attempt": task.attempt},
        )
        return task


def _without_execution(content: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in content.items() if key != "_execution"}
