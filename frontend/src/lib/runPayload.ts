import type { CreateEpisodeRunInput } from "../api/types";

export function createEpisodeRunRequest(input: CreateEpisodeRunInput) {
  if (
    input.writingSamples.length > 0 &&
    (!input.writingStyleConsent.ownershipAttested ||
      !input.writingStyleConsent.modelProcessingConsent)
  ) {
    throw new Error("writing samples require ownership and model-processing consent");
  }
  const writingStyleReference = input.writingSamples.length
    ? {
        version: "writing_style_reference_v1" as const,
        samples: input.writingSamples.map((sample) => ({
          source_id: sample.sourceId,
          sample_kind: sample.sampleKind,
        })),
        ownership_attested: input.writingStyleConsent.ownershipAttested,
        model_processing_consent: input.writingStyleConsent.modelProcessingConsent,
        usage: "style_only" as const,
      }
    : undefined;

  return {
    workflow_type: "episode-research" as const,
    payload: {
      topic: input.topic.trim(),
      source_ids: input.factualSourceIds,
      creative_brief: {
        target_duration_minutes: input.brief.targetDurationMinutes,
        speaking_rate_chars_per_minute: 280,
        scenario: input.brief.scenario,
        target_audience: input.brief.targetAudience.trim(),
        communication_goal: input.brief.communicationGoal.trim(),
        tone: input.brief.tone,
        must_include: input.brief.mustInclude,
        avoid_patterns: input.brief.avoidPatterns,
      },
      draft_quality: { enabled: true, profile: "podcast_draft_v1" as const },
      ...(writingStyleReference
        ? { writing_style_reference: writingStyleReference }
        : {}),
    },
  };
}

export function splitList(value: string): string[] {
  return [...new Set(value.split(/[，,；;\n]/).map((item) => item.trim()).filter(Boolean))];
}
