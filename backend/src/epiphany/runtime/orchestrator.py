from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from epiphany.events import append_event
from epiphany.interview_schemas import BUILD_INTERVIEW_SCAFFOLD
from epiphany.models import Artifact, Run, Task
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

    async def fail_after_task(
        self,
        session: AsyncSession,
        *,
        run: Run,
        failed_task: Task,
    ) -> None:
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
        elif run.workflow_version == INTERVIEW_RESEARCH_WORKFLOW_VERSION:
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
