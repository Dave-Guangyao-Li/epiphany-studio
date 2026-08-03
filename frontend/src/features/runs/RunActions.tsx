import { type FormEvent, useMemo, useRef, useState } from "react";
import { projectsApi, runsApi, sourcesApi } from "../../api/epiphany";
import type {
  EventView,
  ImprovementPlanRecord,
  MaterialReadinessView,
  RunView,
  SupplementalInterviewPlanRecord,
  UserFeedbackRequest,
} from "../../api/types";
import { useNavigate } from "../../app/router";
import { ErrorNotice } from "../../components/ErrorNotice";
import { requestedCheckpoint } from "../../lib/events";

function stableId(prefix: string) {
  return `${prefix}-${crypto.randomUUID().replaceAll("-", "")}`;
}

async function importAnswerSource(
  projectId: string | null,
  title: string,
  text: string,
) {
  const body = { title, source_type: "voice_note_transcript" as const, text };
  return projectId
    ? projectsApi.importSource(projectId, body)
    : sourcesApi.importGlobal(body);
}

export function HumanCheckpointPanel({
  run,
  events,
  readiness,
  onChanged,
}: {
  run: RunView;
  events: EventView[];
  readiness: MaterialReadinessView | null;
  onChanged: () => Promise<void>;
}) {
  const retryRef = useRef<{
    fingerprint: string;
    id: string;
    sourceId?: string;
  } | null>(null);
  const [title, setTitle] = useState("补充口述");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const checkpoint = requestedCheckpoint(events, run.current_step);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    const fingerprint = `${checkpoint}\n${title}\n${text}`;
    if (retryRef.current?.fingerprint !== fingerprint) {
      retryRef.current = { fingerprint, id: stableId("ui-resume") };
    }
    setSubmitting(true);
    setError(null);
    try {
      const sourceId = retryRef.current.sourceId ?? (
        await importAnswerSource(run.project_id, title.trim(), text)
      ).source.id;
      retryRef.current.sourceId = sourceId;
      await runsApi.resume(run.id, {
        checkpoint,
        submission_id: retryRef.current.id,
        source_ids: [sourceId],
      });
      setText("");
      await onChanged();
    } catch (submitError) {
      setError(submitError);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="action-card human-action">
      <p className="eyebrow">HUMAN CHECKPOINT</p>
      <h3>Agent 暂停了，现在轮到你补充。</h3>
      <p>先把口述转成文字贴进来。它会成为新的 Source，之后 Run 才会继续。</p>
      {checkpoint === "material_readiness" && readiness && (
        <div className="readiness-summary" aria-label="素材充足度">
          <div className="readiness-heading">
            <div>
              <strong>素材还不足以稳妥支撑 {readiness.targetDurationMinutes} 分钟</strong>
              <p>
                当前素材估计可支持 {readiness.estimatedSupportedMinutesLow}–{readiness.estimatedSupportedMinutesHigh} 分钟。
                这是素材字符量估算，不是最终录音时长。
              </p>
            </div>
            <span>{readiness.status === "ready" ? "已就绪" : "需要补充"}</span>
          </div>
          <dl className="readiness-metrics">
            <div><dt>当前有效素材</dt><dd>{readiness.currentSourceCharCount.toLocaleString()} 字</dd></div>
            <div><dt>最低需要</dt><dd>{readiness.requiredSourceCharCount.toLocaleString()} 字</dd></div>
            <div><dt>仍需补充</dt><dd>{readiness.additionalSourceCharsNeeded.toLocaleString()} 字</dd></div>
          </dl>
          {readiness.gaps.length > 0 && (
            <div className="readiness-gaps">
              <h4>为什么现在暂停</h4>
              {readiness.gaps.map((gap) => (
                <article key={gap.code}>
                  <strong>{gap.title}</strong>
                  <p>{gap.detail}</p>
                </article>
              ))}
            </div>
          )}
          {readiness.followUpQuestions.length > 0 && (
            <div className="readiness-questions">
              <h4>可以从这些具体问题开始讲</h4>
              <ol>
                {readiness.followUpQuestions.map((question, index) => (
                  <li key={`${index}-${question.prompt}`}>
                    <strong>{question.prompt}</strong>
                    <small>{question.purpose}</small>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
      <form onSubmit={submit}>
        <label>素材标题<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label>
        <label>
          补充口述
          <textarea value={text} onChange={(event) => setText(event.target.value)} rows={9} placeholder="不要急着总结。尽量讲一个具体场景、动作、对话或当时的身体感受。" required />
        </label>
        <div className="form-actions split-actions">
          <span className="checkpoint-chip">checkpoint · {checkpoint}</span>
          <button className="button primary" disabled={submitting || !text.trim()}>
            {submitting ? "正在保存并继续…" : "保存 Source 并 Resume"}
          </button>
        </div>
      </form>
      <ErrorNotice error={error} />
    </section>
  );
}

export function FeedbackPanel({ runId }: { runId: string }) {
  const [overall, setOverall] = useState(4);
  const [voice, setVoice] = useState(4);
  const [recordability, setRecordability] = useState(4);
  const [usefulness, setUsefulness] = useState(4);
  const [tone, setTone] = useState(4);
  const [wouldRecord, setWouldRecord] = useState(false);
  const [duration, setDuration] = useState("");
  const [comment, setComment] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<unknown>(null);
  const submissionRef = useRef(stableId("ui-feedback"));

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const body: UserFeedbackRequest = {
      submission_id: submissionRef.current,
      feedback_origin: "human",
      decision: wouldRecord ? "accepted" : "needs_revision",
      overall_rating: overall,
      voice_match_rating: voice,
      recordability_rating: recordability,
      usefulness_rating: usefulness,
      tone_fit_rating: tone,
      would_record_as_is: wouldRecord,
      observed_duration_minutes: duration ? Number(duration) : null,
      comment: comment.trim() || null,
    };
    try {
      await runsApi.feedback(runId, body);
      setNotice("反馈已作为独立记录保存，不会覆盖原稿。需要修改时可在下方显式创建 Revision。");
    } catch (submitError) {
      setError(submitError);
    }
  }

  const rating = (label: string, value: number, setter: (next: number) => void) => (
    <label className="rating-field">{label}<input type="range" min={1} max={5} value={value} onChange={(event) => setter(Number(event.target.value))} /><strong>{value}/5</strong></label>
  );

  return (
    <section className="action-card">
      <p className="eyebrow">YOUR REVIEW</p><h3>这篇稿子，你真的愿意录吗？</h3>
      <form onSubmit={submit}>
        <div className="ratings-grid">
          {rating("整体", overall, setOverall)}
          {rating("像我", voice, setVoice)}
          {rating("可录", recordability, setRecordability)}
          {rating("有用", usefulness, setUsefulness)}
          {rating("语气", tone, setTone)}
        </div>
        <div className="form-row two-columns">
          <label>真实录制时长 <span className="optional">可选</span><input type="number" min="0.1" max="180" step="0.1" value={duration} onChange={(event) => setDuration(event.target.value)} placeholder="分钟" /></label>
          <label className="check-row"><input type="checkbox" checked={wouldRecord} onChange={(event) => setWouldRecord(event.target.checked)} />我愿意按这一版直接录</label>
        </div>
        <label>具体反馈<textarea rows={3} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="哪一段不像你？哪里还缺一个场景？" /></label>
        <button className="button secondary">保存我的评价</button>
      </form>
      {notice && <p className="form-notice" role="status">{notice}</p>}
      <ErrorNotice error={error} />
    </section>
  );
}

export function ImprovementAnswerPanel({
  run,
  improvement,
}: {
  run: RunView;
  improvement: ImprovementPlanRecord | null;
}) {
  const navigate = useNavigate();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const retryRef = useRef<{
    fingerprint: string;
    id: string;
    sourceId?: string;
  } | null>(null);
  const questions = improvement?.plan.targeted_questions ?? [];
  const offersSupplementalMaterial = (improvement?.plan.options ?? []).some(
    (option) => option.kind === "add_supplemental_material",
  );
  const answered = useMemo(
    () => questions.filter((question) => (
      answers[`${question.anchor_path}:${question.prompt}`]?.trim()
    )),
    [answers, questions],
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!answered.length) return;
    const text = answered.map((question) => (
      `## ${question.prompt}\n\n${answers[`${question.anchor_path}:${question.prompt}`].trim()}`
    )).join("\n\n");
    const fingerprint = `${improvement?.artifact.id ?? "improvement"}\n${text}`;
    if (retryRef.current?.fingerprint !== fingerprint) {
      retryRef.current = { fingerprint, id: stableId("ui-improvement-revision") };
    }
    setSubmitting(true);
    setError(null);
    try {
      const sourceId = retryRef.current.sourceId ?? (
        await importAnswerSource(
          run.project_id,
          "补充采访回答｜初稿后",
          text,
        )
      ).source.id;
      retryRef.current.sourceId = sourceId;
      const response = await runsApi.revision(run.id, {
        version: "draft_revision_request_v2_supplemental_interview",
        submission_id: retryRef.current.id,
        selected_actions: ["add_supplemental_material"],
        selected_feedback_artifact_ids: [],
        selected_gap_codes: [],
        source_ids: [sourceId],
        answered_question_ids: [],
        supplemental_interview_plan_artifact_id: null,
        target_duration_minutes: null,
        revision_instruction: "优先使用本轮回答里的具体场景，保留父稿中已经成立的内容，不用空话补齐时长。",
      });
      navigate(`/runs/${response.run.id}`);
    } catch (submitError) {
      setError(submitError);
    } finally {
      setSubmitting(false);
    }
  }

  if (
    run.workflow_version === "v9" ||
    !offersSupplementalMaterial ||
    questions.length === 0
  ) return null;

  return (
    <section className="action-card supplemental-action">
      <p className="eyebrow">TARGETED FOLLOW-UP</p>
      <h3>稿子还短，可以先回答几个具体问题。</h3>
      <p>这是初稿进入 v9 Revision 的桥梁。只回答真的唤起记忆的问题，不需要为了凑时长把每题都答完。</p>
      <form onSubmit={submit}>
        <div className="question-stack">
          {questions.map((question) => (
            <article key={`${question.anchor_path}:${question.prompt}`}>
              <blockquote>“{question.anchor_text}”</blockquote>
              <label>
                <strong>{question.prompt}</strong>
                <small>{question.purpose}</small>
                <textarea
                  rows={5}
                  aria-label={question.prompt}
                  value={answers[`${question.anchor_path}:${question.prompt}`] ?? ""}
                  onChange={(event) => setAnswers((current) => ({
                    ...current,
                    [`${question.anchor_path}:${question.prompt}`]: event.target.value,
                  }))}
                  placeholder="想到什么就说什么，尽量保留现场细节。"
                />
              </label>
            </article>
          ))}
        </div>
        <button className="button primary" disabled={submitting || !answered.length}>
          {submitting ? "正在保存并创建 Revision…" : `用 ${answered.length} 个回答创建下一版`}
        </button>
      </form>
      <ErrorNotice error={error} />
    </section>
  );
}

export function RevisionPanel({ run, improvement }: { run: RunView; improvement: ImprovementPlanRecord | null }) {
  const navigate = useNavigate();
  const [instruction, setInstruction] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const retryIds = useRef<Record<string, string>>({});
  const options = improvement?.plan.options ?? [];

  async function create(kind: string, suggestedTarget?: number | null) {
    const key = `${kind}:${instruction}:${suggestedTarget ?? ""}`;
    retryIds.current[key] ??= stableId("ui-revision");
    const selectedGapCodes =
      kind === "apply_selected_feedback"
        ? (improvement?.plan.gaps ?? []).filter((gap) => gap.severity !== "info").map((gap) => gap.code)
        : [];
    if (kind === "apply_selected_feedback" && !selectedGapCodes.length) return;
    setCreating(true);
    setError(null);
    try {
      const response = await runsApi.revision(run.id, {
        submission_id: retryIds.current[key],
        selected_actions: [kind],
        selected_feedback_artifact_ids: [],
        selected_gap_codes: selectedGapCodes,
        source_ids: [],
        answered_question_ids: [],
        supplemental_interview_plan_artifact_id: null,
        target_duration_minutes: kind === "lower_target_duration" ? suggestedTarget : null,
        revision_instruction: instruction.trim() || null,
      });
      navigate(`/runs/${response.run.id}`);
    } catch (createError) {
      setError(createError);
    } finally {
      setCreating(false);
    }
  }

  if (!improvement) return null;
  return (
    <section className="action-card">
      <p className="eyebrow">EXPLICIT REVISION</p><h3>保留这一版，再创建一个候选稿。</h3>
      <label>给 Editor 的补充说明 <span className="optional">可选</span><textarea rows={3} value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="比如：开场更快进入具体画面，不要增加没有来源的结论。" /></label>
      <div className="revision-options">
        {options.map((option) => (
          <button
            type="button"
            className={option.recommended ? "revision-option recommended" : "revision-option"}
            disabled={creating || option.kind === "add_supplemental_material"}
            onClick={() => { void create(option.kind, option.suggested_target_duration_minutes); }}
            key={option.kind}
          >
            <span>{option.recommended ? "推荐" : "可选"}</span>
            <strong>{option.kind.replaceAll("_", " ")}</strong>
            <p>{option.explanation}</p>
          </button>
        ))}
      </div>
      <ErrorNotice error={error} />
    </section>
  );
}

export function SupplementalInterviewPanel({
  run,
  record,
}: {
  run: RunView;
  record: SupplementalInterviewPlanRecord;
}) {
  const navigate = useNavigate();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const retryRef = useRef<{
    fingerprint: string;
    id: string;
    sourceId?: string;
  } | null>(null);
  const answered = useMemo(
    () => record.plan.questions.filter((question) => answers[question.question_id]?.trim()),
    [answers, record.plan.questions],
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!answered.length) return;
    const text = answered.map((question) => (
      `## ${question.question_id}｜${question.prompt}\n\n${answers[question.question_id].trim()}`
    )).join("\n\n");
    const fingerprint = `${record.artifact.id}\n${text}`;
    if (retryRef.current?.fingerprint !== fingerprint) {
      retryRef.current = { fingerprint, id: stableId("ui-supplement-revision") };
    }
    setSubmitting(true);
    setError(null);
    try {
      const sourceId = retryRef.current.sourceId ?? (
        await importAnswerSource(
          run.project_id,
          `补充采访回答｜第 ${record.plan.round_number} 轮`,
          text,
        )
      ).source.id;
      retryRef.current.sourceId = sourceId;
      const response = await runsApi.revision(run.id, {
        version: "draft_revision_request_v2_supplemental_interview",
        submission_id: retryRef.current.id,
        selected_actions: ["add_supplemental_material"],
        selected_feedback_artifact_ids: [],
        selected_gap_codes: [],
        source_ids: [sourceId],
        supplemental_interview_plan_artifact_id: record.artifact.id,
        answered_question_ids: answered.map((question) => question.question_id),
        target_duration_minutes: null,
        revision_instruction: "优先使用本轮明确回答的新场景，同时保留父稿中已经成立的内容。",
      });
      navigate(`/runs/${response.run.id}`);
    } catch (submitError) {
      setError(submitError);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="action-card supplemental-action">
      <p className="eyebrow">DRAFT-AWARE INTERVIEW · ROUND {record.plan.round_number}/{record.plan.max_rounds}</p>
      <h3>稿子还短，系统从具体原句里找了几个缺口。</h3>
      <p>不需要每题都答；只回答真的唤起记忆的问题。没有或不记得，也可以跳过。</p>
      <form onSubmit={submit}>
        <div className="question-stack">
          {record.plan.questions.map((question) => (
            <article key={question.question_id}>
              <blockquote>“{question.anchor_quote}”</blockquote>
              <label>
                <strong>{question.prompt}</strong>
                <small>{question.answer_cues.join(" · ")}</small>
                <textarea rows={5} value={answers[question.question_id] ?? ""} onChange={(event) => setAnswers((current) => ({ ...current, [question.question_id]: event.target.value }))} placeholder="想到什么就说什么，尽量保留现场细节。" />
              </label>
            </article>
          ))}
        </div>
        <button className="button primary" disabled={submitting || !answered.length}>
          {submitting ? "正在保存并创建 Revision…" : `用 ${answered.length} 个回答创建下一版`}
        </button>
      </form>
      <ErrorNotice error={error} />
    </section>
  );
}
