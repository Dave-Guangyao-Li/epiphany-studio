import type {
  ArtifactView,
  EventView,
  RunView,
  SourceStarterCandidate,
} from "../api/types";

export type SourceStarterStepStatus = "pending" | "active" | "complete" | "failed";

export interface SourceStarterStep {
  key: "context" | "generate" | "validate" | "edit";
  label: string;
  detail: string;
  status: SourceStarterStepStatus;
}

export function sourceStarterArtifact(run: RunView | null): ArtifactView | null {
  return [...(run?.artifacts ?? [])]
    .reverse()
    .find((item) => item.kind === "source_starter_candidate") ?? null;
}

const terminalFailureStatuses = new Set(["failed", "cancelled", "timed_out"]);

function stringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

export function sourceStarterCandidate(run: RunView | null): SourceStarterCandidate | null {
  const artifact = sourceStarterArtifact(run);
  if (!artifact) return null;
  const content = artifact.content_json;
  const safety = content.safety;
  if (
    content.schema_version !== "source-starter-candidate.v1" ||
    (content.mode !== "exploration_outline" && content.mode !== "starter_draft") ||
    typeof content.starter_text !== "string" ||
    !stringList(content.questions) ||
    !stringList(content.uncertainties) ||
    typeof safety !== "object" || safety === null
  ) return null;
  return content as unknown as SourceStarterCandidate;
}

function saw(events: EventView[], type: string): boolean {
  return events.some((event) => event.type === type);
}

export function sourceStarterWasConfirmed(
  run: RunView | null,
  events: EventView[] = [],
): boolean {
  return (
    (run?.artifacts.some((item) => item.kind === "source_starter_confirmation") ?? false) ||
    saw(events, "workflow.source_starter.confirmed")
  );
}

export function sourceStarterSteps(
  run: RunView | null,
  events: EventView[],
  candidate: SourceStarterCandidate | null,
  confirmed = false,
): SourceStarterStep[] {
  const task = run?.tasks.find((item) => item.kind === "build_source_starter") ?? null;
  const runFailed = run ? terminalFailureStatuses.has(run.status) : false;
  const taskFailed = task ? terminalFailureStatuses.has(task.status) : false;
  const modelStarted =
    (run?.model_calls?.length ?? 0) > 0 || saw(events, "model.call.started");
  const modelCompleted =
    (run?.model_calls?.some((call) => call.status === "succeeded") ?? false) ||
    events.some((event) =>
      event.type === "model.call.completed" && event.payload.status === "succeeded"
    );
  const generationStarted =
    modelStarted ||
    task?.status === "running" ||
    task?.status === "succeeded" ||
    taskFailed ||
    saw(events, "task.started");
  const generationFinished =
    modelCompleted ||
    task?.status === "succeeded" ||
    saw(events, "task.succeeded") ||
    candidate !== null;
  const workflowValidated =
    candidate !== null || saw(events, "workflow.source_starter.completed");
  const persistedConfirmation = confirmed || sourceStarterWasConfirmed(run, events);

  return [
    {
      key: "context",
      label: "准备上下文",
      detail: "读取当前 Project 的名称、描述和你的素材设置",
      // The backend snapshots Project context before it returns the durable
      // Run. Once a Run exists this step is already complete, even if the
      // worker has not started the model task yet.
      status: run ? "complete" : "pending",
    },
    {
      key: "generate",
      label: "模型生成",
      detail: "只生成起步草稿，不会直接保存为 Source",
      status: generationFinished
          ? "complete"
          : taskFailed || (runFailed && generationStarted)
            ? "failed"
            : generationStarted
              ? "active"
              : "pending",
    },
    {
      key: "validate",
      label: "校验结果",
      detail: "检查结构与安全边界，并保存可追踪 Artifact",
      status: runFailed && generationFinished
        ? "failed"
        : workflowValidated
          ? "complete"
          : generationFinished
            ? "active"
            : "pending",
    },
    {
      key: "edit",
      label: "等待你编辑确认",
      detail: persistedConfirmation
        ? "你已确认并导入，Run 的确认记录已持久化"
        : "核对事实、补上个人经历，再确认导入",
      status: persistedConfirmation ? "complete" : candidate ? "active" : "pending",
    },
  ];
}

export const AI_STARTER_SEPARATOR = "\n\n---\n\n";

export function appendSourceStarter(existingText: string, starterText: string): string {
  const existing = existingText.trimEnd();
  const starter = starterText.trim();
  if (!existing) return starter;
  return `${existing}${AI_STARTER_SEPARATOR}${starter}`;
}

export function removeUneditedSourceStarter(
  currentText: string,
  baseText: string,
  starterText: string,
): string | null {
  return currentText === appendSourceStarter(baseText, starterText) ? baseText : null;
}
