import type { EventView } from "../api/types";

export function mergeEvents(current: EventView[], incoming: EventView[]): EventView[] {
  const bySequence = new Map<number, EventView>();
  for (const event of [...current, ...incoming]) {
    if (!bySequence.has(event.sequence)) bySequence.set(event.sequence, event);
  }
  return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
}

export function lastEventSequence(events: EventView[]): number {
  return events.length ? events[events.length - 1].sequence : 0;
}

export function requestedCheckpoint(events: EventView[], currentStep: string | null) {
  const event = [...events]
    .reverse()
    .find((candidate) => candidate.type === "workflow.user_input.requested");
  const checkpoint = event?.payload.checkpoint;
  if (checkpoint === "interview_scaffold" || checkpoint === "material_readiness") {
    return checkpoint;
  }
  return currentStep === "awaiting_interview_response"
    ? "interview_scaffold"
    : "material_readiness";
}

export const DURABLE_EVENT_NAMES = [
  "run.created",
  "run.started",
  "run.waiting_for_user",
  "run.resumed",
  "run.succeeded",
  "run.failed",
  "run.cancelled",
  "task.queued",
  "task.started",
  "task.succeeded",
  "task.failed",
  "task.cancelled",
  "task.retry_scheduled",
  "task.recovered",
  "model.call.started",
  "model.call.completed",
  "model.call.failed",
  "workflow.fan_out.started",
  "workflow.fan_in.waiting",
  "workflow.fan_in.completed",
  "workflow.interview_scaffold.queued",
  "workflow.interview_scaffold.completed",
  "workflow.material_readiness.evaluated",
  "workflow.user_input.requested",
  "workflow.user_material.accepted",
  "workflow.editor.queued",
  "workflow.editor.completed",
  "workflow.draft_quality.completed",
  "workflow.draft_improvement.planned",
  "workflow.draft_revision.requested",
  "workflow.draft_revision.queued",
  "workflow.draft_revision.compared",
  "workflow.draft_supplemental_interview.queued",
  "workflow.draft_supplemental_interview.completed",
  "workflow.draft_supplemental_interview.unavailable",
  "workflow.draft_supplemental_interview.limit_reached",
  "workflow.source_starter.completed",
  "workflow.source_starter.confirmed",
] as const;
