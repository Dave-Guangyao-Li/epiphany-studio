import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SourceSummary } from "../src/api/types";
import { RouterProvider } from "../src/app/router";
import { CreateRunForm } from "../src/features/runs/CreateRunForm";

function source(
  id: string,
  title: string,
  metadata: Record<string, unknown> = {},
  sourceType: SourceSummary["source_type"] = "journal",
): SourceSummary {
  return {
    id,
    title,
    source_type: sourceType,
    content_sha256: `${id}-sha`,
    char_count: 1200,
    segment_count: 3,
    metadata,
    created_at: "2026-07-31T00:00:00Z",
    updated_at: "2026-07-31T00:00:00Z",
  };
}

describe("Create Run writing-sample consent", () => {
  it("requires two explicit confirmations only after a style sample is selected", () => {
    render(
      <RouterProvider>
        <CreateRunForm
          projectId="project_test"
          sources={[source("src_fact", "事实日记"), source("src_style", "说话风格样本")]}
        />
      </RouterProvider>,
    );

    fireEvent.change(screen.getByLabelText("本期主题"), { target: { value: "重新开始记录" } });
    fireEvent.click(screen.getByLabelText("作为事实素材选择：事实日记"));
    const submit = screen.getByRole("button", { name: "启动 Agent Run" });
    expect(submit).toBeEnabled();
    expect(screen.queryByLabelText(/拥有所选样本/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /展开 Creative Brief/ }));
    fireEvent.click(screen.getByLabelText("作为风格样本选择：说话风格样本"));
    expect(submit).toBeDisabled();

    fireEvent.click(screen.getByLabelText(/拥有所选样本/));
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/发送给模型/));
    expect(submit).toBeEnabled();

    fireEvent.click(screen.getByLabelText("作为风格样本选择：说话风格样本"));
    fireEvent.click(screen.getByLabelText("作为风格样本选择：说话风格样本"));
    expect(screen.getByLabelText(/拥有所选样本/)).not.toBeChecked();
    expect(screen.getByLabelText(/发送给模型/)).not.toBeChecked();
    expect(submit).toBeDisabled();
  });

  it("never offers an AI-assisted Source as a writing-style sample", () => {
    render(
      <RouterProvider>
        <CreateRunForm
          projectId="project_test"
          sources={[
            source("src_human", "我的真实日记"),
            source("src_ai", "AI 起步日记", {
              origin: "ai_assisted",
              source_starter_run_id: "run_starter_1",
            }),
          ]}
        />
      </RouterProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /展开 Creative Brief/ }));
    expect(screen.getByLabelText("作为风格样本选择：我的真实日记")).toBeInTheDocument();
    expect(screen.queryByLabelText("作为风格样本选择：AI 起步日记")).not.toBeInTheDocument();
    expect(screen.getByText(/1 份 AI 辅助素材未列入风格样本/)).toBeInTheDocument();
  });

  it("offers writing samples only in the explicit style-only selector", () => {
    render(
      <RouterProvider>
        <CreateRunForm
          projectId="project_test"
          sources={[
            source("src_fact", "真实日记"),
            source("src_style", "过往播客稿", {}, "writing_sample"),
          ]}
        />
      </RouterProvider>,
    );

    expect(screen.getByLabelText("作为事实素材选择：真实日记")).toBeInTheDocument();
    expect(screen.queryByLabelText("作为事实素材选择：过往播客稿")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("作为风格样本选择：过往播客稿")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /展开 Creative Brief/ }));
    expect(screen.getByLabelText("作为风格样本选择：过往播客稿")).toBeInTheDocument();
  });
});
