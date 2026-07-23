from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from epiphany.events import append_event
from epiphany.models import Artifact, Run, Task
from epiphany.state_machine import RunStatus, TaskStatus, validate_run_transition

FAKE_WORKFLOW_STEPS = ("prepare_sources", "fake_research", "assemble_artifact")


class Orchestrator:
    def __init__(self, *, task_max_attempts: int) -> None:
        self.task_max_attempts = task_max_attempts

    async def enqueue_initial_task(self, session: AsyncSession, run: Run) -> Task:
        return await self._enqueue_task(
            session,
            run=run,
            kind=FAKE_WORKFLOW_STEPS[0],
            parent_task_id=None,
            input_json={
                "task_kind": FAKE_WORKFLOW_STEPS[0],
                "run_payload": run.input_json,
            },
        )

    async def advance_after_success(
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

    async def _enqueue_task(
        self,
        session: AsyncSession,
        *,
        run: Run,
        kind: str,
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
            agent_type="fake_agent",
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
