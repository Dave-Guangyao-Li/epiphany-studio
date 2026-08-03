import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type {
  ArtifactView,
  MaterialReadinessView,
  RunView,
} from "../src/api/types";
import { HumanCheckpointPanel } from "../src/features/runs/RunActions";
import { commitForCurrentRunRoute } from "../src/features/runs/RunTracePage";
import {
  latestMaterialReadiness,
  isCurrentRunGeneration,
  redactInternalIds,
  shouldLoadDerivedForRun,
  supportsSupplementalInterview,
} from "../src/lib/runTrace";

function artifact(
  id: string,
  createdAt: string,
  current: number,
  additional: number,
): ArtifactView {
  return {
    id,
    task_id: "task_test",
    kind: "material_readiness_report",
    created_at: createdAt,
    content_json: {
      status: "needs_more_material",
      target_duration_minutes: 15,
      target_script_chars_min: 3570,
      additional_source_chars_needed: additional,
      estimated_supported_minutes_low: 7.2,
      estimated_supported_minutes_high: 9.1,
      counts: { available_source_char_count: current },
      gaps: [{
        code: "insufficient_evidence_volume",
        title: "素材量不足",
        detail: "还缺少 src_abcdef0123456789 能支撑中段的具体场景。",
      }],
      follow_up_questions: [{
        prompt: "回到 seg_0123456789abcdef：你第一次发现旧录音时，房间里有什么声音？",
        purpose: "围绕 src_abcdef0123456789 补充一个可直接口播的现场。",
        source_refs: [{ source_id: "src_private", source_segment_id: "seg_private" }],
      }],
    },
  };
}

function run(overrides: Partial<RunView> = {}): RunView {
  return {
    id: "run_test",
    project_id: "project_test",
    parent_run_id: null,
    workflow_type: "episode-research",
    workflow_version: "v8",
    status: "succeeded",
    current_step: "completed",
    output_artifact_id: null,
    model_call_count: 0,
    cancel_requested_at: null,
    created_at: "2026-07-31T00:00:00Z",
    updated_at: "2026-07-31T00:00:00Z",
    input_json: {},
    tasks: [],
    artifacts: [],
    model_calls: [],
    ...overrides,
  };
}

describe("Run Trace loading guards", () => {
  it("loads derived records once per succeeded Run and supplemental plans only for v9", () => {
    const succeeded = run();
    expect(shouldLoadDerivedForRun(succeeded, null)).toBe(true);
    expect(shouldLoadDerivedForRun(succeeded, succeeded.id)).toBe(false);
    expect(shouldLoadDerivedForRun(run({ status: "running" }), null)).toBe(false);
    expect(supportsSupplementalInterview("v8")).toBe(false);
    expect(supportsSupplementalInterview("v9")).toBe(true);
  });

  it("rejects late responses from an older route generation", () => {
    expect(isCurrentRunGeneration(
      { runId: "run_old", generation: 2 },
      { runId: "run_new", generation: 3 },
    )).toBe(false);
    expect(isCurrentRunGeneration(
      { runId: "run_same", generation: 2 },
      { runId: "run_same", generation: 3 },
    )).toBe(false);
    expect(isCurrentRunGeneration(
      { runId: "run_same", generation: 3 },
      { runId: "run_same", generation: 3 },
    )).toBe(true);
  });

  it("does not commit late cancel or Markdown state into a newer Run route", () => {
    const oldRoute = { runId: "run_old", generation: 4 };
    const newRoute = { runId: "run_new", generation: 5 };
    const committed: string[] = [];

    expect(commitForCurrentRunRoute(oldRoute, newRoute, () => committed.push("cancel")))
      .toBe(false);
    expect(commitForCurrentRunRoute(oldRoute, newRoute, () => committed.push("markdown")))
      .toBe(false);
    expect(committed).toEqual([]);

    expect(commitForCurrentRunRoute(newRoute, newRoute, () => committed.push("current")))
      .toBe(true);
    expect(committed).toEqual(["current"]);
  });
});

describe("material readiness checkpoint", () => {
  it("selects the latest report and strips internal references", () => {
    const result = latestMaterialReadiness([
      artifact("artifact_old", "2026-07-31T00:00:00Z", 800, 2770),
      artifact("artifact_new", "2026-07-31T00:01:00Z", 1450, 2120),
    ]);
    expect(result).toMatchObject({
      currentSourceCharCount: 1450,
      requiredSourceCharCount: 3570,
      additionalSourceCharsNeeded: 2120,
    });
    expect(JSON.stringify(result)).not.toContain("src_private");
    expect(JSON.stringify(result)).not.toContain("seg_private");
    expect(JSON.stringify(result)).not.toContain("src_abcdef0123456789");
    expect(JSON.stringify(result)).not.toContain("seg_0123456789abcdef");
  });

  it("redacts internal IDs embedded in human-facing sentences", () => {
    expect(redactInternalIds(
      "请展开 src_abcdef0123456789 对应的 seg_0123456789abcdef 场景",
    )).toBe("请展开 [内部来源] 对应的 [内部片段] 场景");
  });

  it("shows counts, gap details, and concrete questions without raw IDs", () => {
    const readiness: MaterialReadinessView = latestMaterialReadiness([
      artifact("artifact_latest", "2026-07-31T00:00:00Z", 1450, 2120),
    ])!;
    render(
      <HumanCheckpointPanel
        run={run({ status: "waiting_for_user", current_step: "awaiting_more_material" })}
        events={[{
          id: "event_1",
          run_id: "run_test",
          task_id: null,
          sequence: 1,
          type: "workflow.user_input.requested",
          payload: { checkpoint: "material_readiness" },
          created_at: "2026-07-31T00:00:00Z",
        }]}
        readiness={readiness}
        onChanged={async () => undefined}
      />,
    );

    expect(screen.getByText("1,450 字")).toBeInTheDocument();
    expect(screen.getByText("3,570 字")).toBeInTheDocument();
    expect(screen.getByText("2,120 字")).toBeInTheDocument();
    expect(screen.getByText("还缺少 [内部来源] 能支撑中段的具体场景。")).toBeInTheDocument();
    expect(screen.getByText("回到 [内部片段]：你第一次发现旧录音时，房间里有什么声音？")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("src_private");
    expect(document.body.textContent).not.toContain("seg_private");
  });
});
