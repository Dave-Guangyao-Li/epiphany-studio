import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { runsApi } from "../src/api/epiphany";
import type {
  DraftUserFeedbackRecord,
  ImprovementPlanRecord,
  RunView,
} from "../src/api/types";
import { RouterProvider } from "../src/app/router";
import { ApplyFeedbackPanel } from "../src/features/runs/RunActions";

function run(): RunView {
  return {
    id: "run_parent",
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
    updated_at: "2026-08-20T00:00:01Z",
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
    created_at: "2026-08-20T00:00:01Z",
  },
  plan: { options: [], gaps: [] },
};

function feedbackRecord(
  id: string,
  origin: "human" | "synthetic_test",
  comment: string,
): DraftUserFeedbackRecord {
  return {
    artifact: {
      id,
      task_id: null,
      kind: "draft_user_feedback",
      content_json: {},
      created_at: "2026-08-20T00:00:02Z",
    },
    feedback: {
      schema_version: "draft_user_feedback_v1",
      submission_id: `sub_${id}`,
      draft_artifact_id: "artifact_draft",
      feedback_origin: origin,
      human_signal_eligible: origin === "human",
      decision: "needs_revision",
      overall_rating: 3,
      voice_match_rating: 4,
      recordability_rating: 3,
      usefulness_rating: 4,
      tone_fit_rating: 4,
      would_record_as_is: false,
      observed_duration_minutes: 7.5,
      comment,
    },
  };
}

beforeEach(() => {
  vi.stubGlobal("scrollTo", vi.fn());
  window.history.replaceState(null, "", "/runs/run_parent");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("apply selected feedback panel", () => {
  it("creates an apply_selected_feedback revision from the chosen human feedback", async () => {
    const revision = vi.spyOn(runsApi, "revision").mockResolvedValue({
      idempotent_replay: false,
      request_artifact_id: "artifact_request",
      run: { ...run(), id: "run_child", parent_run_id: "run_parent", workflow_version: "v9" },
    });

    render(
      <RouterProvider>
        <ApplyFeedbackPanel
          run={run()}
          improvement={improvement}
          feedback={[
            feedbackRecord("artifact_feedback_human", "human", "结尾几段有点空泛。"),
            feedbackRecord("artifact_feedback_synthetic", "synthetic_test", "合成测试反馈。"),
          ]}
        />
      </RouterProvider>,
    );

    expect(screen.getByText("把你保存的反馈用到下一版。")).toBeInTheDocument();
    // synthetic_test feedback must never be applied as if it were human judgment.
    expect(screen.queryByLabelText("应用反馈 artifact_feedback_synthetic")).not.toBeInTheDocument();

    const submit = screen.getByRole("button", { name: "用 0 条反馈创建下一版" });
    expect(submit).toBeDisabled();

    fireEvent.click(screen.getByLabelText("应用反馈 artifact_feedback_human"));
    fireEvent.click(screen.getByRole("button", { name: "用 1 条反馈创建下一版" }));

    await waitFor(() => expect(revision).toHaveBeenCalledWith(
      "run_parent",
      expect.objectContaining({
        selected_actions: ["apply_selected_feedback"],
        selected_feedback_artifact_ids: ["artifact_feedback_human"],
        selected_gap_codes: [],
        source_ids: [],
      }),
    ));
    expect(window.location.pathname).toBe("/runs/run_child");
  });

  it("reuses the submission id when revision creation is retried", async () => {
    const revision = vi.spyOn(runsApi, "revision")
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce({
        idempotent_replay: false,
        request_artifact_id: "artifact_request",
        run: { ...run(), id: "run_child", parent_run_id: "run_parent", workflow_version: "v9" },
      });

    render(
      <RouterProvider>
        <ApplyFeedbackPanel
          run={run()}
          improvement={improvement}
          feedback={[feedbackRecord("artifact_feedback_human", "human", "结尾几段有点空泛。")]}
        />
      </RouterProvider>,
    );

    fireEvent.click(screen.getByLabelText("应用反馈 artifact_feedback_human"));
    fireEvent.click(screen.getByRole("button", { name: "用 1 条反馈创建下一版" }));
    await screen.findByText("temporary failure");

    fireEvent.click(screen.getByRole("button", { name: "用 1 条反馈创建下一版" }));
    await waitFor(() => expect(revision).toHaveBeenCalledTimes(2));

    expect(revision.mock.calls[1][1].submission_id).toBe(revision.mock.calls[0][1].submission_id);
    expect(window.location.pathname).toBe("/runs/run_child");
  });

  it("renders nothing without an improvement plan or without human feedback", () => {
    const { rerender } = render(
      <RouterProvider>
        <ApplyFeedbackPanel
          run={run()}
          improvement={null}
          feedback={[feedbackRecord("artifact_feedback_human", "human", "结尾几段有点空泛。")]}
        />
      </RouterProvider>,
    );
    expect(screen.queryByText("把你保存的反馈用到下一版。")).not.toBeInTheDocument();

    rerender(
      <RouterProvider>
        <ApplyFeedbackPanel
          run={run()}
          improvement={improvement}
          feedback={[feedbackRecord("artifact_feedback_synthetic", "synthetic_test", "合成测试反馈。")]}
        />
      </RouterProvider>,
    );
    expect(screen.queryByText("把你保存的反馈用到下一版。")).not.toBeInTheDocument();
  });
});
