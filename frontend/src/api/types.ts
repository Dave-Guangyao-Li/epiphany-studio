export type SourceType =
  | "journal"
  | "podcast_draft"
  | "voice_note_transcript"
  | "writing_sample"
  | "other";

export type RunStatus =
  | "queued"
  | "running"
  | "waiting_for_user"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface ProjectSummary {
  id: string;
  title: string;
  description: string | null;
  source_count: number;
  run_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectDetail extends ProjectSummary {
  sources: SourceSummary[];
  runs: RunSummary[];
}

export interface SourceSummary {
  id: string;
  title: string;
  source_type: SourceType;
  content_sha256: string;
  char_count: number;
  segment_count: number;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SourceSegment {
  id: string;
  source_id: string;
  position: number;
  text: string;
  char_start: number;
  char_end: number;
  content_sha256: string;
  created_at: string;
}

export interface SourceDetail extends SourceSummary {
  segments: SourceSegment[];
}

export interface ImportSourceResponse {
  created: boolean;
  linked?: boolean;
  source: SourceDetail;
}

export type SourceStarterMode = "exploration_outline" | "starter_draft";
export type SourceStarterSourceType = "journal" | "podcast_draft" | "other";

export interface CreateSourceStarterRequest {
  submission_id: string;
  source_title: string | null;
  source_type: SourceStarterSourceType;
  mode: SourceStarterMode;
  intent: string | null;
}

export interface SourceStarterCandidate {
  schema_version: "source-starter-candidate.v1";
  mode: SourceStarterMode;
  source_title: string | null;
  source_type: SourceStarterSourceType;
  starter_text: string;
  questions: string[];
  uncertainties: string[];
  safety: {
    requires_user_confirmation: true;
    factual_claims_require_verification: true;
  };
  _execution?: Record<string, unknown>;
}

export interface ConfirmSourceStarterResponse extends ImportSourceResponse {
  idempotent_replay: boolean;
  source_starter_run_id: string;
  candidate_artifact_id: string;
  confirmation_artifact_id: string;
}

export interface RunSummary {
  id: string;
  project_id: string | null;
  parent_run_id: string | null;
  workflow_type: string;
  workflow_version: string;
  status: RunStatus;
  current_step: string | null;
  output_artifact_id: string | null;
  model_call_count: number;
  cancel_requested_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskView {
  id: string;
  parent_task_id: string | null;
  kind: string;
  agent_type: string;
  status: string;
  attempt: number;
  max_attempts: number;
  output_artifact_id: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface ArtifactView {
  id: string;
  task_id: string | null;
  kind: string;
  content_json: Record<string, unknown>;
  created_at: string;
}

export interface ModelCallView {
  id: string;
  task_id: string;
  attempt: number;
  provider: string;
  model: string;
  status: string;
  input_tokens: number;
  output_tokens: number;
  duration_ms: number | null;
  estimated_cost_micros: number;
  cost_currency: string;
  error_code: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface RunView extends RunSummary {
  input_json: Record<string, unknown>;
  tasks: TaskView[];
  artifacts: ArtifactView[];
  model_calls: ModelCallView[];
}

export interface EventView {
  id: string;
  run_id: string;
  task_id: string | null;
  sequence: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export type EpisodeScenario =
  | "reflective_solo"
  | "narrative_solo"
  | "educational_explainer"
  | "conversational_diary";

export interface CreativeBriefForm {
  targetDurationMinutes: 10 | 15 | 30;
  scenario: EpisodeScenario;
  targetAudience: string;
  communicationGoal: string;
  tone: string[];
  mustInclude: string[];
  avoidPatterns: string[];
}

export interface CreateEpisodeRunInput {
  topic: string;
  factualSourceIds: string[];
  writingSamples: Array<{
    sourceId: string;
    sampleKind: "written_prose" | "spoken_transcript";
  }>;
  writingStyleConsent: {
    ownershipAttested: boolean;
    modelProcessingConsent: boolean;
  };
  brief: CreativeBriefForm;
}

export interface QualityReportRecord {
  report: Record<string, unknown>;
  artifact: ArtifactView;
}

export interface MaterialReadinessView {
  status: "ready" | "needs_more_material";
  targetDurationMinutes: number;
  currentSourceCharCount: number;
  requiredSourceCharCount: number;
  additionalSourceCharsNeeded: number;
  estimatedSupportedMinutesLow: number;
  estimatedSupportedMinutesHigh: number;
  gaps: Array<{
    code: string;
    title: string;
    detail: string;
  }>;
  followUpQuestions: Array<{
    prompt: string;
    purpose: string;
  }>;
}

export interface ImprovementPlanRecord {
  plan: {
    options?: Array<{
      kind: string;
      recommended: boolean;
      explanation: string;
      source_refs?: Array<{ source_id: string; source_segment_id: string }>;
      suggested_target_duration_minutes?: number | null;
    }>;
    gaps?: Array<{ code: string; severity: string; explanation: string }>;
    targeted_questions?: Array<{
      prompt: string;
      purpose: string;
      anchor_kind: "material_gap" | "scaffold_question";
      anchor_path: string;
      anchor_text: string;
      keywords: string[];
      source_refs: Array<{ source_id: string; source_segment_id: string }>;
    }>;
    [key: string]: unknown;
  };
  artifact: ArtifactView;
}

export interface SupplementalQuestion {
  question_id: string;
  anchor_quote: string;
  prompt: string;
  purpose: string;
  detail_type: string;
  answer_cues: string[];
}

export interface SupplementalInterviewPlanRecord {
  plan: {
    questions: SupplementalQuestion[];
    round_number: 1 | 2;
    max_rounds: 2;
    status: "awaiting_user";
    duration_gap: Record<string, number>;
    [key: string]: unknown;
  };
  artifact: ArtifactView;
}

export interface ResumeRunResponse {
  resumed: boolean;
  idempotent_replay: boolean;
  submission_artifact_id: string;
  run: RunView;
}

export interface CreateRevisionResponse {
  idempotent_replay: boolean;
  request_artifact_id: string;
  run: RunView;
}

export interface UserFeedbackRequest {
  submission_id: string;
  feedback_origin: "human";
  decision: "accepted" | "needs_revision" | "rejected";
  overall_rating: number;
  voice_match_rating: number;
  recordability_rating: number;
  usefulness_rating: number;
  tone_fit_rating: number;
  would_record_as_is: boolean;
  observed_duration_minutes: number | null;
  comment: string | null;
}
