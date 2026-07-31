import { describe, expect, it } from "vitest";
import type { EventView } from "../src/api/types";
import { lastEventSequence, mergeEvents, requestedCheckpoint } from "../src/lib/events";

function event(sequence: number, type = "task.started"): EventView {
  return {
    id: `evt_${sequence}`,
    run_id: "run_test",
    task_id: null,
    sequence,
    type,
    payload: {},
    created_at: "2026-07-31T00:00:00Z",
  };
}

describe("durable event reducer", () => {
  it("sorts by sequence and removes SSE/replay duplicates", () => {
    const result = mergeEvents([event(2), event(1)], [event(2), event(3)]);
    expect(result.map((item) => item.sequence)).toEqual([1, 2, 3]);
    expect(lastEventSequence(result)).toBe(3);
  });

  it("uses the latest persisted checkpoint instead of guessing from status", () => {
    const events = [
      event(1),
      { ...event(2, "workflow.user_input.requested"), payload: { checkpoint: "material_readiness" } },
    ];
    expect(requestedCheckpoint(events, "awaiting_interview_response")).toBe("material_readiness");
  });
});
