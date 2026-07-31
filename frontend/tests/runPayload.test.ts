import { describe, expect, it } from "vitest";
import { createEpisodeRunRequest, splitList } from "../src/lib/runPayload";

describe("episode Run payload", () => {
  it("keeps factual sources separate from consented style samples", () => {
    const request = createEpisodeRunRequest({
      topic: " 五年后重新打开播客 ",
      factualSourceIds: ["src_fact_1", "src_fact_2"],
      writingSamples: [{ sourceId: "src_style", sampleKind: "spoken_transcript" }],
      writingStyleConsent: {
        ownershipAttested: true,
        modelProcessingConsent: true,
      },
      brief: {
        targetDurationMinutes: 15,
        scenario: "reflective_solo",
        targetAudience: "正在经历转折的人",
        communicationGoal: "讲清楚重新开始的原因",
        tone: ["真诚", "自然口语"],
        mustInclude: ["旧录音"],
        avoidPatterns: ["强行升华"],
      },
    });

    expect(request.workflow_type).toBe("episode-research");
    expect(request.payload.topic).toBe("五年后重新打开播客");
    expect(request.payload.source_ids).toEqual(["src_fact_1", "src_fact_2"]);
    expect(request.payload.creative_brief.target_duration_minutes).toBe(15);
    expect(request.payload.writing_style_reference).toMatchObject({
      ownership_attested: true,
      model_processing_consent: true,
      usage: "style_only",
      samples: [{ source_id: "src_style", sample_kind: "spoken_transcript" }],
    });
  });

  it("refuses style samples without both explicit confirmations", () => {
    expect(() => createEpisodeRunRequest({
      topic: "测试",
      factualSourceIds: ["src_fact"],
      writingSamples: [{ sourceId: "src_style", sampleKind: "written_prose" }],
      writingStyleConsent: {
        ownershipAttested: true,
        modelProcessingConsent: false,
      },
      brief: {
        targetDurationMinutes: 10,
        scenario: "reflective_solo",
        targetAudience: "听众",
        communicationGoal: "说明变化",
        tone: ["自然"],
        mustInclude: [],
        avoidPatterns: [],
      },
    })).toThrow(/ownership and model-processing consent/);
  });

  it("does not require or send a consent contract without style samples", () => {
    const request = createEpisodeRunRequest({
      topic: "只使用事实素材",
      factualSourceIds: ["src_fact"],
      writingSamples: [],
      writingStyleConsent: {
        ownershipAttested: false,
        modelProcessingConsent: false,
      },
      brief: {
        targetDurationMinutes: 10,
        scenario: "reflective_solo",
        targetAudience: "听众",
        communicationGoal: "说明变化",
        tone: ["自然"],
        mustInclude: [],
        avoidPatterns: [],
      },
    });
    expect(request.payload).not.toHaveProperty("writing_style_reference");
  });

  it("normalizes comma/newline lists and removes duplicates", () => {
    expect(splitList("真诚，克制, 真诚\n自然口语")).toEqual(["真诚", "克制", "自然口语"]);
  });
});
