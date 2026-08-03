import { apiOptional, apiRequest, apiText } from "./client";
import type {
  CreateEpisodeRunInput,
  CreateRevisionResponse,
  EventView,
  ImportSourceResponse,
  ImprovementPlanRecord,
  ProjectDetail,
  ProjectSummary,
  QualityReportRecord,
  ResumeRunResponse,
  RunView,
  SourceDetail,
  SourceType,
  SupplementalInterviewPlanRecord,
  UserFeedbackRequest,
} from "./types";
import { createEpisodeRunRequest } from "../lib/runPayload";

export const projectsApi = {
  list: () => apiRequest<ProjectSummary[]>("/projects"),
  get: (projectId: string) => apiRequest<ProjectDetail>(`/projects/${projectId}`),
  create: (title: string, description: string) =>
    apiRequest<ProjectSummary>("/projects", {
      method: "POST",
      body: JSON.stringify({ title, description: description.trim() || null }),
    }),
  importSource: (
    projectId: string,
    body: { title: string; source_type: SourceType; text: string },
  ) =>
    apiRequest<ImportSourceResponse>(`/projects/${projectId}/sources`, {
      method: "POST",
      body: JSON.stringify({ ...body, metadata: {} }),
    }),
  createRun: (projectId: string, input: CreateEpisodeRunInput, submissionId: string) =>
    apiRequest<RunView>(`/projects/${projectId}/runs`, {
      method: "POST",
      body: JSON.stringify({
        submission_id: submissionId,
        ...createEpisodeRunRequest(input),
      }),
    }),
};

export const sourcesApi = {
  get: (sourceId: string) => apiRequest<SourceDetail>(`/sources/${sourceId}`),
  importGlobal: (body: { title: string; source_type: SourceType; text: string }) =>
    apiRequest<ImportSourceResponse>("/sources", {
      method: "POST",
      body: JSON.stringify({ ...body, metadata: {} }),
    }),
};

export const runsApi = {
  get: (runId: string) => apiRequest<RunView>(`/runs/${runId}`),
  events: (runId: string, after = 0) =>
    apiRequest<EventView[]>(`/runs/${runId}/events?after=${after}`),
  cancel: (runId: string) =>
    apiRequest<RunView>(`/runs/${runId}/cancel`, { method: "POST" }),
  resume: (
    runId: string,
    body: {
      checkpoint: "interview_scaffold" | "material_readiness";
      submission_id: string;
      source_ids: string[];
    },
  ) =>
    apiRequest<ResumeRunResponse>(`/runs/${runId}/resume`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  quality: (runId: string) =>
    apiOptional<QualityReportRecord>(`/runs/${runId}/quality-report`),
  improvement: (runId: string) =>
    apiOptional<ImprovementPlanRecord>(`/runs/${runId}/improvement-plan`),
  supplemental: (runId: string) =>
    apiOptional<SupplementalInterviewPlanRecord>(
      `/runs/${runId}/supplemental-interview-plan`,
    ),
  scaffoldMarkdown: (runId: string) =>
    apiText(`/runs/${runId}/exports/interview-scaffold.md`),
  draftMarkdown: (runId: string) => apiText(`/runs/${runId}/exports/podcast-draft.md`),
  showNotesMarkdown: (runId: string) => apiText(`/runs/${runId}/exports/show-notes.md`),
  qualityMarkdown: (runId: string) => apiText(`/runs/${runId}/exports/quality-report.md`),
  feedback: (runId: string, body: UserFeedbackRequest) =>
    apiRequest<Record<string, unknown>>(`/runs/${runId}/quality-feedback`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  revision: (runId: string, body: Record<string, unknown>) =>
    apiRequest<CreateRevisionResponse>(`/runs/${runId}/revisions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
