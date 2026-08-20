import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { runsApi } from "../src/api/epiphany";
import type { ArtifactView, InterviewScaffold, RunView } from "../src/api/types";
import { RouterProvider } from "../src/app/router";
import { ScaffoldEditorPanel } from "../src/features/runs/ScaffoldEditor";

const REF = { source_id: "src_1", source_segment_id: "seg_1" };

function statement(text: string) {
  return { text, source_refs: [REF] };
}

function section(title: string, prompt: string) {
  return {
    title,
    source_refs: [REF],
    known_context: [statement("已知背景。")],
    transition: statement("过渡语。"),
    questions: [
      { prompt, purpose: "追问目的。", keywords: ["关键词"], source_refs: [REF] },
    ],
  };
}

function scaffold(openingText: string): InterviewScaffold {
  return {
    title: "五年后重新开始记录生活",
    episode_intent: statement("本期意图。"),
    opening: statement(openingText),
    sections: [section("小节一", "第一个问题？"), section("小节二", "第二个问题？")],
    material_gaps: [],
    closing: statement("收尾。"),
  };
}

function scaffoldArtifact(): ArtifactView {
  return {
    id: "art_scaffold",
    task_id: "task_interviewer",
    kind: "build_interview_scaffold_result",
    content_json: { ...scaffold("原始 AI 开场白。"), _execution: { worker: "x" } },
    created_at: "2026-08-20T00:00:00Z",
  };
}

function humanEditArtifact(): ArtifactView {
  return {
    id: "art_edit",
    task_id: null,
    kind: "interview_scaffold_human_edit",
    content_json: {
      schema_version: "interview_scaffold_human_edit_v1",
      submission_id: "sub_1",
      base_artifact_id: "art_scaffold",
      editor_origin: "human",
      scaffold: scaffold("人工编辑过的开场白。"),
    },
    created_at: "2026-08-20T00:01:00Z",
  };
}

function run(artifacts: ArtifactView[]): RunView {
  return {
    id: "run_scaffold",
    project_id: "project_test",
    parent_run_id: null,
    workflow_type: "episode-research",
    workflow_version: "v8",
    status: "succeeded",
    current_step: "complete",
    output_artifact_id: "artifact_draft",
    model_call_count: 5,
    cancel_requested_at: null,
    created_at: "2026-08-20T00:00:00Z",
    updated_at: "2026-08-20T00:00:02Z",
    input_json: {},
    tasks: [],
    artifacts,
    model_calls: [],
  };
}

beforeEach(() => {
  vi.stubGlobal("scrollTo", vi.fn());
  window.history.replaceState(null, "", "/runs/run_scaffold");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("interview scaffold editor", () => {
  it("saves edited text as a human-edited version, preserving base id and citations", async () => {
    const save = vi.spyOn(runsApi, "saveScaffoldEdit").mockResolvedValue({
      idempotent_replay: false,
      edit: {
        schema_version: "interview_scaffold_human_edit_v1",
        submission_id: "sub",
        base_artifact_id: "art_scaffold",
        editor_origin: "human",
        scaffold: scaffold("人工新的开场白。"),
      },
      artifact: humanEditArtifact(),
    });

    render(
      <RouterProvider>
        <ScaffoldEditorPanel run={run([scaffoldArtifact()])} />
      </RouterProvider>,
    );

    const opening = screen.getByLabelText("开场白");
    expect(opening).toHaveValue("原始 AI 开场白。");
    fireEvent.change(opening, { target: { value: "人工新的开场白。" } });
    fireEvent.change(screen.getByLabelText("小节 1 · 问题 1"), {
      target: { value: "人工修改后的第一个问题？" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存人工编辑版" }));

    await waitFor(() => expect(save).toHaveBeenCalledWith(
      "run_scaffold",
      expect.objectContaining({
        base_artifact_id: "art_scaffold",
        scaffold: expect.objectContaining({
          title: "五年后重新开始记录生活",
          opening: expect.objectContaining({
            text: "人工新的开场白。",
            source_refs: [REF],
          }),
        }),
      }),
    ));
    const body = save.mock.calls[0][1];
    expect(body.scaffold.sections[0].questions[0].prompt).toBe("人工修改后的第一个问题？");
    // Citations and structure are carried through unchanged.
    expect(body.scaffold.sections[0].questions[0].source_refs).toEqual([REF]);
    expect(body.scaffold.sections).toHaveLength(2);
    await screen.findByText(/已保存为人工编辑版/);
  });

  it("shows the latest human-edited version and an edited badge", () => {
    render(
      <RouterProvider>
        <ScaffoldEditorPanel run={run([scaffoldArtifact(), humanEditArtifact()])} />
      </RouterProvider>,
    );
    expect(screen.getByLabelText("开场白")).toHaveValue("人工编辑过的开场白。");
    expect(screen.getByText(/已编辑（人工版）/)).toBeInTheDocument();
  });

  it("renders nothing when the run has no interview scaffold", () => {
    render(
      <RouterProvider>
        <ScaffoldEditorPanel run={run([])} />
      </RouterProvider>,
    );
    expect(screen.queryByText("保存人工编辑版")).not.toBeInTheDocument();
  });
});
