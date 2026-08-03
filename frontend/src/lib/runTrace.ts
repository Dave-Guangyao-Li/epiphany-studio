import type { ArtifactView, MaterialReadinessView, RunView } from "../api/types";

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function redactInternalIds(value: string): string {
  return value
    .replace(/\bsrc_[0-9a-f]{8,}\b/gi, "[内部来源]")
    .replace(/\bseg_[0-9a-f]{8,}\b/gi, "[内部片段]");
}

function displayString(value: unknown): string | null {
  const parsed = stringValue(value);
  return parsed ? redactInternalIds(parsed) : null;
}

function parseMaterialReadiness(content: Record<string, unknown>): MaterialReadinessView | null {
  const counts = content.counts;
  const status = content.status;
  const targetDurationMinutes = numberValue(content.target_duration_minutes);
  const currentSourceCharCount =
    typeof counts === "object" && counts !== null
      ? numberValue((counts as Record<string, unknown>).available_source_char_count)
      : null;
  const requiredSourceCharCount = numberValue(content.target_script_chars_min);
  const additionalSourceCharsNeeded = numberValue(content.additional_source_chars_needed);
  const estimatedSupportedMinutesLow = numberValue(content.estimated_supported_minutes_low);
  const estimatedSupportedMinutesHigh = numberValue(content.estimated_supported_minutes_high);

  if (
    (status !== "ready" && status !== "needs_more_material") ||
    targetDurationMinutes === null ||
    currentSourceCharCount === null ||
    requiredSourceCharCount === null ||
    additionalSourceCharsNeeded === null ||
    estimatedSupportedMinutesLow === null ||
    estimatedSupportedMinutesHigh === null
  ) {
    return null;
  }

  const gaps = Array.isArray(content.gaps)
    ? content.gaps.flatMap((candidate) => {
        if (typeof candidate !== "object" || candidate === null) return [];
        const raw = candidate as Record<string, unknown>;
        const code = stringValue(raw.code);
        const title = displayString(raw.title);
        const detail = displayString(raw.detail);
        return code && title && detail ? [{ code, title, detail }] : [];
      })
    : [];
  const followUpQuestions = Array.isArray(content.follow_up_questions)
    ? content.follow_up_questions.flatMap((candidate) => {
        if (typeof candidate !== "object" || candidate === null) return [];
        const raw = candidate as Record<string, unknown>;
        const prompt = displayString(raw.prompt);
        const purpose = displayString(raw.purpose);
        // Deliberately omit source_refs: internal Source/Segment IDs belong in the
        // trace, not in the human checkpoint prompt.
        return prompt && purpose ? [{ prompt, purpose }] : [];
      })
    : [];

  return {
    status,
    targetDurationMinutes,
    currentSourceCharCount,
    requiredSourceCharCount,
    additionalSourceCharsNeeded,
    estimatedSupportedMinutesLow,
    estimatedSupportedMinutesHigh,
    gaps,
    followUpQuestions,
  };
}

export function latestMaterialReadiness(
  artifacts: ArtifactView[],
): MaterialReadinessView | null {
  const candidates = artifacts
    .map((artifact, index) => ({ artifact, index }))
    .filter(({ artifact }) => artifact.kind === "material_readiness_report")
    .sort((left, right) => {
      const timeDifference =
        new Date(left.artifact.created_at).getTime() -
        new Date(right.artifact.created_at).getTime();
      return timeDifference || left.index - right.index;
    });

  for (let index = candidates.length - 1; index >= 0; index -= 1) {
    const parsed = parseMaterialReadiness(candidates[index].artifact.content_json);
    if (parsed) return parsed;
  }
  return null;
}

export function supportsSupplementalInterview(workflowVersion: string): boolean {
  return workflowVersion === "v9";
}

export function shouldLoadDerivedForRun(
  run: RunView,
  alreadyRequestedRunId: string | null,
): boolean {
  return run.status === "succeeded" && alreadyRequestedRunId !== run.id;
}

export interface RunRouteGeneration {
  runId: string;
  generation: number;
}

export function isCurrentRunGeneration(
  requested: RunRouteGeneration,
  current: RunRouteGeneration,
): boolean {
  return requested.runId === current.runId && requested.generation === current.generation;
}
