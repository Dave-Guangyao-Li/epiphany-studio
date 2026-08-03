import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { projectsApi, runsApi } from "../src/api/epiphany";
import type { ImprovementPlanRecord, RunView } from "../src/api/types";
import { RouterProvider } from "../src/app/router";
import { ImprovementAnswerPanel } from "../src/features/runs/RunActions";

function run(workflowVersion = "v8"): RunView {
  return {
    id: "run_parent",
    project_id: "project_test",
    parent_run_id: null,
    workflow_type: "episode-research",
    workflow_version: workflowVersion,
    status: "succeeded",
    current_step: "complete",
    output_artifact_id: "artifact_draft",
    model_call_count: 5,
    cancel_requested_at: null,
    created_at: "2026-08-03T00:00:00Z",
    updated_at: "2026-08-03T00:00:01Z",
    input_json: {},
    tasks: [],
    artifacts: [],
    model_calls: [],
  };
}

const improvement: ImprovementPlanRecord = {
  artifact: {
    id: "artifact_improvement",
    task_id: "task_editor",
    kind: "draft_improvement_plan",
    content_json: {},
    created_at: "2026-08-03T00:00:01Z",
  },
  plan: {
    options: [{
      kind: "add_supplemental_material",
      recommended: true,
      explanation: "还需要一个具体场景。",
    }],
    targeted_questions: [{
      prompt: "那通电话结束以后，你在房间里做了什么？",
      purpose: "补齐情绪转折后的动作。",
      anchor_kind: "material_gap",
      anchor_path: "material_gaps[0]",
      anchor_text: "成都朋友那通电话",
      keywords: ["电话", "房间"],
      source_refs: [{ source_id: "source_1", source_segment_id: "segment_1" }],
    }],
  },
};

beforeEach(() => {
  vi.stubGlobal("scrollTo", vi.fn());
  window.history.replaceState(null, "", "/runs/run_parent");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("v8 improvement answer bridge", () => {
  it("persists concrete answers as a Source and creates a v9 child Revision", async () => {
    const importSource = vi.spyOn(projectsApi, "importSource").mockResolvedValue({
      created: true,
      linked: true,
      source: {
        id: "source_answer",
        title: "补充采访回答｜初稿后",
        source_type: "voice_note_transcript",
        content_sha256: "sha",
        char_count: 45,
        segment_count: 1,
        metadata: {},
        created_at: "2026-08-03T00:00:02Z",
        updated_at: "2026-08-03T00:00:02Z",
        segments: [],
      },
    });
    const revision = vi.spyOn(runsApi, "revision").mockResolvedValue({
      idempotent_replay: false,
      request_artifact_id: "artifact_request",
      run: { ...run("v9"), id: "run_child", parent_run_id: "run_parent" },
    });

    render(
      <RouterProvider>
        <ImprovementAnswerPanel run={run()} improvement={improvement} />
      </RouterProvider>,
    );

    expect(screen.getByText("稿子还短，可以先回答几个具体问题。")).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "用 0 个回答创建下一版" });
    expect(submit).toBeDisabled();

    fireEvent.change(
      screen.getByLabelText("那通电话结束以后，你在房间里做了什么？"),
      { target: { value: "我没有马上开灯，先把已经凉掉的饺子收进冰箱。" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "用 1 个回答创建下一版" }));

    await waitFor(() => expect(importSource).toHaveBeenCalledWith(
      "project_test",
      expect.objectContaining({
        title: "补充采访回答｜初稿后",
        source_type: "voice_note_transcript",
        text: expect.stringContaining("我没有马上开灯"),
      }),
    ));
    await waitFor(() => expect(revision).toHaveBeenCalledWith(
      "run_parent",
      expect.objectContaining({
        version: "draft_revision_request_v2_supplemental_interview",
        selected_actions: ["add_supplemental_material"],
        source_ids: ["source_answer"],
        answered_question_ids: [],
        supplemental_interview_plan_artifact_id: null,
      }),
    ));
    expect(window.location.pathname).toBe("/runs/run_child");
  });

  it("reuses the imported Source and submission id when Revision creation is retried", async () => {
    const importSource = vi.spyOn(projectsApi, "importSource").mockResolvedValue({
      created: true,
      linked: true,
      source: {
        id: "source_answer",
        title: "补充采访回答｜初稿后",
        source_type: "voice_note_transcript",
        content_sha256: "sha",
        char_count: 45,
        segment_count: 1,
        metadata: {},
        created_at: "2026-08-03T00:00:02Z",
        updated_at: "2026-08-03T00:00:02Z",
        segments: [],
      },
    });
    const revision = vi.spyOn(runsApi, "revision")
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce({
        idempotent_replay: false,
        request_artifact_id: "artifact_request",
        run: { ...run("v9"), id: "run_child", parent_run_id: "run_parent" },
      });

    render(
      <RouterProvider>
        <ImprovementAnswerPanel run={run()} improvement={improvement} />
      </RouterProvider>,
    );

    fireEvent.change(
      screen.getByLabelText("那通电话结束以后，你在房间里做了什么？"),
      { target: { value: "我没有马上开灯，先把已经凉掉的饺子收进冰箱。" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "用 1 个回答创建下一版" }));
    await screen.findByText("temporary failure");

    fireEvent.click(screen.getByRole("button", { name: "用 1 个回答创建下一版" }));
    await waitFor(() => expect(revision).toHaveBeenCalledTimes(2));

    expect(importSource).toHaveBeenCalledTimes(1);
    const firstBody = revision.mock.calls[0][1];
    const secondBody = revision.mock.calls[1][1];
    expect(secondBody).toEqual(expect.objectContaining({
      submission_id: firstBody.submission_id,
      source_ids: ["source_answer"],
      version: "draft_revision_request_v2_supplemental_interview",
    }));
    expect(window.location.pathname).toBe("/runs/run_child");
  });

  it("does not duplicate the bridge once a v9 interview planner owns the loop", () => {
    render(
      <RouterProvider>
        <ImprovementAnswerPanel run={run("v9")} improvement={improvement} />
      </RouterProvider>,
    );
    expect(screen.queryByText("稿子还短，可以先回答几个具体问题。")).not.toBeInTheDocument();
  });
});
