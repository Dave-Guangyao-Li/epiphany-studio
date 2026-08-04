import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { projectsApi, runsApi } from "../src/api/epiphany";
import type {
  ProjectDetail,
  RunStatus,
  RunView,
  SourceStarterCandidate,
} from "../src/api/types";
import { RouterProvider } from "../src/app/router";
import { SourceImporter } from "../src/features/sources/SourceImporter";

const candidate: SourceStarterCandidate = {
  schema_version: "source-starter-candidate.v1",
  mode: "exploration_outline",
  source_title: null,
  source_type: "journal",
  starter_text: "我现在对潜水还不熟悉，最先吸引我的是水下世界的安静。",
  questions: ["最早是什么画面让你想了解潜水？"],
  uncertainties: ["具体的潜水经历仍需本人补充"],
  safety: {
    requires_user_confirmation: true,
    factual_claims_require_verification: true,
  },
};

function succeededRun(): RunView {
  return {
    id: "run_starter_1",
    project_id: "project_test",
    parent_run_id: null,
    workflow_type: "source-starter",
    workflow_version: "v1",
    status: "succeeded",
    current_step: "build_source_starter",
    output_artifact_id: "art_candidate",
    model_call_count: 1,
    cancel_requested_at: null,
    created_at: "2026-08-03T00:00:00Z",
    updated_at: "2026-08-03T00:00:01Z",
    input_json: {},
    tasks: [{
      id: "task_starter_1",
      parent_task_id: null,
      kind: "build_source_starter",
      agent_type: "source_starter",
      status: "succeeded",
      attempt: 1,
      max_attempts: 2,
      output_artifact_id: "art_candidate",
      error_code: null,
      error_message: null,
      created_at: "2026-08-03T00:00:00Z",
      updated_at: "2026-08-03T00:00:01Z",
    }],
    artifacts: [{
      id: "art_candidate",
      task_id: "task_starter_1",
      kind: "source_starter_candidate",
      content_json: candidate as unknown as Record<string, unknown>,
      created_at: "2026-08-03T00:00:01Z",
    }],
    model_calls: [],
  };
}

function runningRun(status: RunStatus = "running", withCandidate = false): RunView {
  const completed = status === "waiting_for_user" || status === "succeeded";
  return {
    ...succeededRun(),
    id: "run_starter_running",
    status,
    output_artifact_id: withCandidate ? "art_candidate" : null,
    tasks: [{
      ...succeededRun().tasks[0],
      id: "task_starter_running",
      status: completed ? "succeeded" : "running",
      output_artifact_id: withCandidate ? "art_candidate" : null,
    }],
    artifacts: withCandidate ? succeededRun().artifacts : [],
  };
}

function projectDetail(runs: ProjectDetail["runs"] = []): ProjectDetail {
  return {
    id: "project_test",
    title: "测试 Project",
    description: null,
    source_count: 0,
    run_count: runs.length,
    sources: [],
    runs,
    created_at: "2026-08-03T00:00:00Z",
    updated_at: "2026-08-03T00:00:00Z",
  };
}

function renderImporter(
  onImported = vi.fn(),
  project = projectDetail(),
) {
  vi.spyOn(projectsApi, "get").mockResolvedValue(project);
  return render(
    <RouterProvider>
      <SourceImporter projectId="project_test" onImported={onImported} />
    </RouterProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SourceImporter AI starter", () => {
  it("generates without a title, appends to existing text, and confirms provenance on import", async () => {
    const createStarter = vi.spyOn(projectsApi, "createSourceStarter").mockResolvedValue(succeededRun());
    vi.spyOn(runsApi, "events").mockResolvedValue([
      {
        id: "evt_1",
        run_id: "run_starter_1",
        task_id: "task_starter_1",
        sequence: 1,
        type: "workflow.source_starter.completed",
        payload: {},
        created_at: "2026-08-03T00:00:01Z",
      },
    ]);
    const confirm = vi.spyOn(projectsApi, "confirmSourceStarter").mockResolvedValue({
      created: true,
      linked: true,
      idempotent_replay: false,
      source_starter_run_id: "run_starter_1",
      candidate_artifact_id: "art_candidate",
      confirmation_artifact_id: "art_confirmation",
      source: {
        id: "src_1",
        title: "为什么想学潜水",
        source_type: "journal",
        content_sha256: "sha",
        char_count: 66,
        segment_count: 1,
        metadata: { origin: "ai_assisted" },
        created_at: "2026-08-03T00:00:02Z",
        updated_at: "2026-08-03T00:00:02Z",
        segments: [],
      },
    });
    const imported = vi.fn();
    renderImporter(imported);

    await waitFor(() => expect(
      screen.getByRole("button", { name: "✨ 帮我起个头" }),
    ).toBeEnabled());
    fireEvent.change(screen.getByLabelText("正文"), { target: { value: "我先记下一点自己的直觉。" } });
    fireEvent.click(screen.getByRole("button", { name: "✨ 帮我起个头" }));

    await waitFor(() => expect(createStarter).toHaveBeenCalled());
    expect(createStarter.mock.calls[0][1]).toMatchObject({
      source_title: null,
      source_type: "journal",
      mode: "exploration_outline",
    });
    await waitFor(() => expect(
      (screen.getByLabelText("正文") as HTMLTextAreaElement).value,
    ).toContain(candidate.starter_text));
    expect(screen.getByLabelText("正文")).toHaveValue(
      `我先记下一点自己的直觉。\n\n---\n\n${candidate.starter_text}`,
    );
    expect(screen.getByText("准备上下文").closest("li")).toHaveClass("starter-step-complete");
    expect(screen.getByText("等待你编辑确认").closest("li")).toHaveClass("starter-step-active");
    expect(screen.getByRole("button", { name: "确认并导入 Source" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "为什么想学潜水" } });
    fireEvent.click(screen.getByLabelText(/我已检查这份 AI 草稿/));
    fireEvent.click(screen.getByRole("button", { name: "确认并导入 Source" }));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith(
      "project_test",
      "run_starter_1",
      expect.objectContaining({
        title: "为什么想学潜水",
        text: expect.stringContaining(candidate.starter_text),
      }),
    ));
    expect(imported).toHaveBeenCalledTimes(1);
  });

  it("does not offer AI generation for writing samples or voice transcripts", async () => {
    renderImporter();
    await waitFor(() => expect(
      screen.getByRole("button", { name: "✨ 帮我起个头" }),
    ).toBeEnabled());

    fireEvent.change(screen.getByLabelText("类型"), { target: { value: "writing_sample" } });
    expect(screen.queryByRole("button", { name: "✨ 帮我起个头" })).not.toBeInTheDocument();
    expect(screen.getByText(/写作样本必须来自你本人/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("类型"), { target: { value: "voice_note_transcript" } });
    expect(screen.queryByRole("button", { name: "✨ 帮我起个头" })).not.toBeInTheDocument();
    expect(screen.getByText(/口述转写必须来自真实口述/)).toBeInTheDocument();
  });

  it("keeps all user input when generation fails", async () => {
    vi.spyOn(projectsApi, "createSourceStarter").mockRejectedValue(new Error("provider unavailable"));
    renderImporter();
    await waitFor(() => expect(
      screen.getByRole("button", { name: "✨ 帮我起个头" }),
    ).toBeEnabled());
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "潜水学习" } });
    fireEvent.change(screen.getByLabelText("正文"), { target: { value: "这段不能丢。" } });
    fireEvent.click(screen.getByRole("button", { name: "✨ 帮我起个头" }));

    expect(await screen.findByText("provider unavailable")).toBeInTheDocument();
    expect(screen.getByLabelText("标题")).toHaveValue("潜水学习");
    expect(screen.getByLabelText("正文")).toHaveValue("这段不能丢。");
  });

  it("retries a polling failure with GET only and never repeats the create POST", async () => {
    const createStarter = vi.spyOn(projectsApi, "createSourceStarter")
      .mockResolvedValue(runningRun());
    vi.spyOn(runsApi, "events").mockResolvedValue([]);
    const getRun = vi.spyOn(runsApi, "get")
      .mockRejectedValueOnce(new Error("temporary disconnect"))
      .mockResolvedValue(succeededRun());
    renderImporter();

    await waitFor(() => expect(
      screen.getByRole("button", { name: "✨ 帮我起个头" }),
    ).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "✨ 帮我起个头" }));

    expect(await screen.findByText("temporary disconnect")).toBeInTheDocument();
    expect(screen.getByText(/重试只读取当前 Project/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(screen.getByText("AI 草稿 · 尚未保存为素材")).toBeInTheDocument());
    expect(getRun).toHaveBeenCalledTimes(2);
    expect(createStarter).toHaveBeenCalledTimes(1);
  });

  it("restores a waiting candidate from Project Run history after refresh", async () => {
    const waiting = runningRun("waiting_for_user", true);
    waiting.input_json = {
      mode: "starter_draft",
      intent: "想先梳理耳压、失控感和第一次体验前要查证的问题",
    };
    waiting.artifacts[0].content_json = {
      ...candidate,
      mode: "starter_draft",
      source_title: "潜水探索提纲",
      source_type: "podcast_draft",
    } as unknown as Record<string, unknown>;
    vi.spyOn(runsApi, "get").mockResolvedValue(waiting);
    vi.spyOn(runsApi, "events").mockResolvedValue([]);
    renderImporter(vi.fn(), projectDetail([waiting]));

    await waitFor(() => expect(screen.getByLabelText("正文")).toHaveValue(candidate.starter_text));
    expect(screen.getByLabelText("标题")).toHaveValue("潜水探索提纲");
    expect(screen.getByLabelText("类型")).toHaveValue("podcast_draft");
    expect(screen.getByLabelText("起步方式")).toHaveValue("starter_draft");
    expect(screen.getByLabelText(/特别想探索什么/)).toHaveValue(
      "想先梳理耳压、失控感和第一次体验前要查证的问题",
    );
    expect(screen.getByText(/已从 Run Artifact 恢复/)).toBeInTheDocument();
    expect(screen.getByText("等待你编辑确认").closest("li")).toHaveClass("starter-step-active");
    expect(screen.getByRole("button", { name: "确认并导入 Source" })).toBeDisabled();
  });

  it("does not resurrect a candidate from a failed or explicitly cancelled Run", async () => {
    const cancelled = runningRun("cancelled", true);
    vi.spyOn(runsApi, "get").mockResolvedValue(cancelled);
    vi.spyOn(runsApi, "events").mockResolvedValue([]);
    renderImporter(vi.fn(), projectDetail([cancelled]));

    await waitFor(() => expect(
      screen.getByRole("button", { name: "✨ 帮我起个头" }),
    ).toBeEnabled());
    expect(screen.queryByText("AI 草稿 · 尚未保存为素材")).not.toBeInTheDocument();
    expect(screen.getByLabelText("正文")).toHaveValue("");
  });

  it("never overwrites text entered while a previous candidate is being recovered", async () => {
    const waiting = runningRun("waiting_for_user", true);
    let resolveProject!: (project: ProjectDetail) => void;
    vi.spyOn(projectsApi, "get").mockReturnValue(new Promise((resolve) => {
      resolveProject = resolve;
    }));
    vi.spyOn(runsApi, "get").mockResolvedValue(waiting);
    vi.spyOn(runsApi, "events").mockResolvedValue([]);
    render(
      <RouterProvider>
        <SourceImporter projectId="project_test" onImported={vi.fn()} />
      </RouterProvider>,
    );

    fireEvent.change(screen.getByLabelText("正文"), { target: { value: "我刚刚输入的真实内容。" } });
    resolveProject(projectDetail([waiting]));

    expect(await screen.findByText(/为避免覆盖你刚输入的正文/)).toBeInTheDocument();
    expect(screen.getByLabelText("正文")).toHaveValue("我刚刚输入的真实内容。");
    expect(screen.getByText(candidate.starter_text)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "追加候选到正文" }));
    expect(screen.getByLabelText("正文")).toHaveValue(
      `我刚刚输入的真实内容。\n\n---\n\n${candidate.starter_text}`,
    );
  });

  it("does not append a second candidate after the first candidate was edited", async () => {
    const createStarter = vi.spyOn(projectsApi, "createSourceStarter")
      .mockResolvedValue(succeededRun());
    vi.spyOn(runsApi, "events").mockResolvedValue([]);
    renderImporter();

    await waitFor(() => expect(
      screen.getByRole("button", { name: "✨ 帮我起个头" }),
    ).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "✨ 帮我起个头" }));
    await waitFor(() => expect(screen.getByLabelText("正文")).toHaveValue(candidate.starter_text));
    fireEvent.change(screen.getByLabelText("正文"), {
      target: { value: `${candidate.starter_text}\n这是我的修改。` },
    });
    fireEvent.click(screen.getByRole("button", { name: "✨ 重新生成（新调用）" }));

    expect(await screen.findByText(/你已经编辑过这份 AI 候选/)).toBeInTheDocument();
    expect(screen.getByLabelText("正文")).toHaveValue(`${candidate.starter_text}\n这是我的修改。`);
    expect(createStarter).toHaveBeenCalledTimes(1);
  });

  it("abandons safely when cancellation succeeded but its response was lost", async () => {
    const waiting = runningRun("waiting_for_user", true);
    const cancelled = {
      ...waiting,
      status: "cancelled",
      tasks: waiting.tasks.map((task) => ({ ...task, status: "cancelled" })),
    } as RunView;
    const createStarter = vi.spyOn(projectsApi, "createSourceStarter")
      .mockResolvedValue(waiting);
    vi.spyOn(runsApi, "events").mockResolvedValue([]);
    const cancel = vi.spyOn(runsApi, "cancel")
      .mockRejectedValue(new Error("cancel response disconnected"));
    const getRun = vi.spyOn(runsApi, "get").mockResolvedValue(cancelled);
    renderImporter();

    await waitFor(() => expect(
      screen.getByRole("button", { name: "✨ 帮我起个头" }),
    ).toBeEnabled());
    fireEvent.change(screen.getByLabelText("正文"), { target: { value: "我的原有文字。" } });
    fireEvent.click(screen.getByRole("button", { name: "✨ 帮我起个头" }));
    await waitFor(() => expect(screen.getByLabelText("正文")).toHaveValue(
      `我的原有文字。\n\n---\n\n${candidate.starter_text}`,
    ));

    fireEvent.click(screen.getByRole("button", { name: "放弃当前 Run" }));

    expect(await screen.findByText(/已放弃当前 AI 起步 Run/)).toBeInTheDocument();
    expect(screen.getByLabelText("正文")).toHaveValue("我的原有文字。");
    expect(screen.queryByText("AI 草稿 · 尚未保存为素材")).not.toBeInTheDocument();
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(getRun).toHaveBeenCalledTimes(1);
    expect(createStarter).toHaveBeenCalledTimes(1);
  });

  it("regenerates exactly once after reconciling a lost cancellation response", async () => {
    const waiting = runningRun("waiting_for_user", true);
    const cancelled = {
      ...waiting,
      status: "cancelled",
      tasks: waiting.tasks.map((task) => ({ ...task, status: "cancelled" })),
    } as RunView;
    const createStarter = vi.spyOn(projectsApi, "createSourceStarter")
      .mockResolvedValueOnce(waiting)
      .mockResolvedValueOnce(succeededRun());
    vi.spyOn(runsApi, "events").mockResolvedValue([]);
    const cancel = vi.spyOn(runsApi, "cancel")
      .mockRejectedValue(new Error("cancel response disconnected"));
    const getRun = vi.spyOn(runsApi, "get").mockResolvedValue(cancelled);
    renderImporter();

    await waitFor(() => expect(
      screen.getByRole("button", { name: "✨ 帮我起个头" }),
    ).toBeEnabled());
    fireEvent.change(screen.getByLabelText("正文"), { target: { value: "我的原有文字。" } });
    fireEvent.click(screen.getByRole("button", { name: "✨ 帮我起个头" }));
    const regenerate = await screen.findByRole("button", { name: "✨ 重新生成（新调用）" });
    fireEvent.click(regenerate);
    // A rapid second click must not create a second replacement Run.
    fireEvent.click(regenerate);

    await waitFor(() => expect(createStarter).toHaveBeenCalledTimes(2));
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(getRun).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("正文")).toHaveValue(
      `我的原有文字。\n\n---\n\n${candidate.starter_text}`,
    );
    expect(screen.queryByText("cancel response disconnected")).not.toBeInTheDocument();
  });
});
