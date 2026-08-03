import { describe, expect, it } from "vitest";
import type { RunView, SourceStarterCandidate } from "../src/api/types";
import {
  appendSourceStarter,
  removeUneditedSourceStarter,
  sourceStarterSteps,
} from "../src/lib/sourceStarter";

const candidate = {
  schema_version: "source-starter-candidate.v1",
  mode: "exploration_outline",
  source_title: null,
  source_type: "journal",
  starter_text: "候选文字",
  questions: [],
  uncertainties: [],
  safety: {
    requires_user_confirmation: true,
    factual_claims_require_verification: true,
  },
} satisfies SourceStarterCandidate;

describe("source starter helpers", () => {
  it("never overwrites existing text and only removes an unedited insertion", () => {
    const combined = appendSourceStarter("用户原文", "AI 草稿");
    expect(combined).toBe("用户原文\n\n---\n\nAI 草稿");
    expect(removeUneditedSourceStarter(combined, "用户原文", "AI 草稿")).toBe("用户原文");
    expect(removeUneditedSourceStarter(`${combined}（已修改）`, "用户原文", "AI 草稿")).toBeNull();
  });

  it("derives visible progress from persisted task and artifact state", () => {
    const run = {
      status: "succeeded",
      tasks: [{ kind: "build_source_starter", status: "succeeded" }],
      artifacts: [],
    } as unknown as RunView;
    const steps = sourceStarterSteps(run, [], candidate, false);
    expect(steps.map((step) => step.status)).toEqual([
      "complete",
      "complete",
      "complete",
      "active",
    ]);
  });

  it("marks context complete as soon as the durable Run exists", () => {
    const run = {
      status: "queued",
      tasks: [{ kind: "build_source_starter", status: "queued" }],
      artifacts: [],
      model_calls: [],
    } as unknown as RunView;
    expect(sourceStarterSteps(run, [], null).map((step) => step.status)).toEqual([
      "complete",
      "pending",
      "pending",
      "pending",
    ]);
  });

  it("shows validation only after a persisted model-call completion", () => {
    const run = {
      status: "running",
      tasks: [{ kind: "build_source_starter", status: "running" }],
      artifacts: [],
      model_calls: [{ status: "succeeded" }],
    } as unknown as RunView;
    expect(sourceStarterSteps(run, [], null).map((step) => step.status)).toEqual([
      "complete",
      "complete",
      "active",
      "pending",
    ]);
  });

  it("completes the human-confirmation step only from persisted confirmation evidence", () => {
    const run = {
      status: "succeeded",
      tasks: [{ kind: "build_source_starter", status: "succeeded" }],
      artifacts: [{ kind: "source_starter_confirmation" }],
      model_calls: [],
    } as unknown as RunView;
    expect(sourceStarterSteps(run, [], candidate).at(-1)?.status).toBe("complete");
  });
});
