import { type FormEvent, useMemo, useRef, useState } from "react";
import { projectsApi } from "../../api/epiphany";
import type {
  CreateEpisodeRunInput,
  EpisodeScenario,
  SourceSummary,
} from "../../api/types";
import { useNavigate } from "../../app/router";
import { ErrorNotice } from "../../components/ErrorNotice";
import { createEpisodeRunRequest, splitList } from "../../lib/runPayload";

function submissionId() {
  const id = crypto.randomUUID().replaceAll("-", "");
  return `ui-run-${id}`;
}

function isAiAssistedSource(source: SourceSummary): boolean {
  return (
    source.metadata.origin === "ai_assisted" ||
    typeof source.metadata.source_starter_run_id === "string"
  );
}

export function CreateRunForm({ projectId, sources }: { projectId: string; sources: SourceSummary[] }) {
  const navigate = useNavigate();
  const retryRef = useRef<{ fingerprint: string; submissionId: string } | null>(null);
  const [topic, setTopic] = useState("");
  const [factualIds, setFactualIds] = useState<string[]>([]);
  const [styleIds, setStyleIds] = useState<string[]>([]);
  const [ownershipAttested, setOwnershipAttested] = useState(false);
  const [modelProcessingConsent, setModelProcessingConsent] = useState(false);
  const [duration, setDuration] = useState<10 | 15 | 30>(10);
  const [scenario, setScenario] = useState<EpisodeScenario>("reflective_solo");
  const [audience, setAudience] = useState("未来的自己，以及正在经历相似转折的听众");
  const [goal, setGoal] = useState("用有来源的具体经历回答本期主题");
  const [tone, setTone] = useState("真诚，克制，自然口语");
  const [mustInclude, setMustInclude] = useState("");
  const [avoidPatterns, setAvoidPatterns] = useState("空泛总结，大量排比，强行升华");
  const [advanced, setAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const selectedCount = factualIds.length + styleIds.length;
  const eligibleStyleSources = useMemo(
    () => sources.filter((source) => !isAiAssistedSource(source)),
    [sources],
  );
  const hiddenAiStyleSourceCount = sources.length - eligibleStyleSources.length;
  const input = useMemo<CreateEpisodeRunInput>(() => ({
    topic,
    factualSourceIds: factualIds,
    writingSamples: styleIds.map((sourceId) => ({
      sourceId,
      sampleKind:
        sources.find((source) => source.id === sourceId)?.source_type === "voice_note_transcript"
          ? "spoken_transcript"
          : "written_prose",
    })),
    writingStyleConsent: {
      ownershipAttested,
      modelProcessingConsent,
    },
    brief: {
      targetDurationMinutes: duration,
      scenario,
      targetAudience: audience,
      communicationGoal: goal,
      tone: splitList(tone).slice(0, 3),
      mustInclude: splitList(mustInclude).slice(0, 10),
      avoidPatterns: splitList(avoidPatterns).slice(0, 10),
    },
  }), [audience, avoidPatterns, duration, factualIds, goal, modelProcessingConsent, mustInclude, ownershipAttested, scenario, sources, styleIds, tone, topic]);

  function resetStyleConsent() {
    setOwnershipAttested(false);
    setModelProcessingConsent(false);
  }

  const styleConsentReady =
    !styleIds.length || (ownershipAttested && modelProcessingConsent);

  function toggleFactual(sourceId: string) {
    if (styleIds.includes(sourceId)) resetStyleConsent();
    setStyleIds((current) => current.filter((id) => id !== sourceId));
    setFactualIds((current) =>
      current.includes(sourceId) ? current.filter((id) => id !== sourceId) : [...current, sourceId],
    );
  }

  function toggleStyle(sourceId: string) {
    // Consent applies to the exact selected set. Adding, removing, or replacing
    // a sample requires a fresh confirmation instead of carrying old consent.
    resetStyleConsent();
    setFactualIds((current) => current.filter((id) => id !== sourceId));
    setStyleIds((current) =>
      current.includes(sourceId) ? current.filter((id) => id !== sourceId) : [...current, sourceId],
    );
  }

  async function createRun(event: FormEvent) {
    event.preventDefault();
    if (!topic.trim() || !factualIds.length || !styleConsentReady) return;
    const fingerprint = JSON.stringify(createEpisodeRunRequest(input));
    if (retryRef.current?.fingerprint !== fingerprint) {
      retryRef.current = { fingerprint, submissionId: submissionId() };
    }
    setSubmitting(true);
    setError(null);
    try {
      const run = await projectsApi.createRun(projectId, input, retryRef.current.submissionId);
      navigate(`/runs/${run.id}`);
    } catch (createError) {
      setError(createError);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="run-builder" onSubmit={createRun}>
      <div className="section-kicker">CREATE A RUN</div>
      <h2>这一期想回答什么？</h2>
      <label>
        本期主题
        <input
          value={topic}
          onChange={(event) => setTopic(event.target.value)}
          placeholder="比如：为什么五年后我重新开始记录生活"
          maxLength={200}
          required
        />
      </label>

      <fieldset>
        <legend>事实素材 <span>至少 1 份</span></legend>
        <div className="source-choice-list">
          {sources.map((source) => (
            <label className={`source-choice ${factualIds.includes(source.id) ? "selected" : ""}`} key={source.id}>
              <input
                type="checkbox"
                aria-label={`作为事实素材选择：${source.title}`}
                checked={factualIds.includes(source.id)}
                disabled={!factualIds.includes(source.id) && factualIds.length >= 20}
                onChange={() => toggleFactual(source.id)}
              />
              <span><strong>{source.title}</strong><small>{source.char_count.toLocaleString()} 字 · {source.source_type}</small></span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="form-row two-columns">
        <label>
          目标时长
          <select value={duration} onChange={(event) => setDuration(Number(event.target.value) as 10 | 15 | 30)}>
            <option value={10}>10 分钟</option>
            <option value={15}>15 分钟</option>
            <option value={30}>30 分钟</option>
          </select>
        </label>
        <label>
          表达场景
          <select value={scenario} onChange={(event) => setScenario(event.target.value as EpisodeScenario)}>
            <option value="reflective_solo">反思型单口</option>
            <option value="narrative_solo">叙事型单口</option>
            <option value="conversational_diary">聊天式日记</option>
            <option value="educational_explainer">解释型内容</option>
          </select>
        </label>
      </div>

      <button type="button" className="text-button" onClick={() => setAdvanced((value) => !value)}>
        {advanced ? "收起" : "展开"} Creative Brief 与写作样本
      </button>

      {advanced && (
        <div className="advanced-panel">
          <label>面向听众<textarea value={audience} onChange={(event) => setAudience(event.target.value)} rows={2} /></label>
          <label>沟通目标<textarea value={goal} onChange={(event) => setGoal(event.target.value)} rows={2} /></label>
          <label>语气（逗号分隔，最多 3 个）<input value={tone} onChange={(event) => setTone(event.target.value)} /></label>
          <label>必须包含<input value={mustInclude} onChange={(event) => setMustInclude(event.target.value)} placeholder="场景、观点或原话" /></label>
          <label>避免模式<input value={avoidPatterns} onChange={(event) => setAvoidPatterns(event.target.value)} /></label>
          <fieldset>
            <legend>仅参考表达风格 <span>可选，不作为事实</span></legend>
            <div className="source-choice-list compact">
              {eligibleStyleSources.map((source) => (
                <label className={`source-choice ${styleIds.includes(source.id) ? "selected style" : ""}`} key={source.id}>
                  <input
                    type="checkbox"
                    aria-label={`作为风格样本选择：${source.title}`}
                    checked={styleIds.includes(source.id)}
                    disabled={!styleIds.includes(source.id) && styleIds.length >= 5}
                    onChange={() => toggleStyle(source.id)}
                  />
                  <span><strong>{source.title}</strong><small>{source.source_type}</small></span>
                </label>
              ))}
            </div>
            {hiddenAiStyleSourceCount > 0 && (
              <p className="ai-style-boundary">
                {hiddenAiStyleSourceCount} 份 AI 辅助素材未列入风格样本。风格模仿只使用你本人真实写过或说过的内容。
              </p>
            )}
          </fieldset>
          {styleIds.length > 0 && (
            <fieldset className="style-consent-panel">
              <legend>写作样本授权确认 <span>两项都确认后才能启动</span></legend>
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={ownershipAttested}
                  onChange={(event) => setOwnershipAttested(event.target.checked)}
                />
                我确认自己拥有所选样本，或已获得使用这些内容的权限
              </label>
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={modelProcessingConsent}
                  onChange={(event) => setModelProcessingConsent(event.target.checked)}
                />
                我同意将所选样本发送给模型，仅用于分析表达风格，不作为本期事实来源
              </label>
            </fieldset>
          )}
        </div>
      )}

      <div className="run-submit-row">
        <span>{selectedCount ? `已选择 ${factualIds.length} 份事实素材${styleIds.length ? `、${styleIds.length} 份风格样本` : ""}` : "先选择事实素材"}</span>
        <button className="button primary" disabled={submitting || !topic.trim() || !factualIds.length || !styleConsentReady}>
          {submitting ? "正在创建…" : "启动 Agent Run"}
        </button>
      </div>
      <ErrorNotice error={error} />
    </form>
  );
}
