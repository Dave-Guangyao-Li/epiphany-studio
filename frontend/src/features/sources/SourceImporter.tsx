import {
  type ChangeEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { projectsApi, runsApi } from "../../api/epiphany";
import type {
  EventView,
  ImportSourceResponse,
  RunSummary,
  RunView,
  SourceStarterCandidate,
  SourceStarterMode,
  SourceStarterSourceType,
  SourceType,
} from "../../api/types";
import { Link } from "../../app/router";
import { ErrorNotice } from "../../components/ErrorNotice";
import { lastEventSequence, mergeEvents } from "../../lib/events";
import {
  appendSourceStarter,
  removeUneditedSourceStarter,
  sourceStarterArtifact,
  sourceStarterCandidate,
  sourceStarterSteps,
  sourceStarterWasConfirmed,
} from "../../lib/sourceStarter";

const terminalRunStatuses = new Set(["succeeded", "failed", "cancelled"]);
const pollingPausedStatuses = new Set([
  "waiting_for_user",
  "succeeded",
  "failed",
  "cancelled",
]);

function isStarterSourceType(value: SourceType): value is SourceStarterSourceType {
  return value === "journal" || value === "podcast_draft" || value === "other";
}

function submissionId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID().replaceAll("-", "")}`;
}

function starterFailure(run: RunView): Error | null {
  if (run.status !== "failed" && run.status !== "cancelled") return null;
  const failedTask = run.tasks.find((task) =>
    task.kind === "build_source_starter" &&
    ["failed", "cancelled", "timed_out"].includes(task.status)
  );
  return new Error(
    failedTask?.error_message ||
      (run.status === "cancelled"
        ? "这次 AI 起步 Run 已取消，正文和表单内容仍然保留。"
        : "这次 AI 起步 Run 没有完成，正文和表单内容仍然保留。"),
  );
}

function newestFirst(left: RunSummary, right: RunSummary) {
  return Date.parse(right.created_at) - Date.parse(left.created_at);
}

export function SourceImporter({
  projectId,
  onImported,
}: {
  projectId: string;
  onImported: (result: ImportSourceResponse) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const sequenceRef = useRef(0);
  const pollTimerRef = useRef<number | null>(null);
  const requestGenerationRef = useRef(0);
  const starterMutationRef = useRef(false);
  const starterSubmissionRef = useRef<string | null>(null);
  const confirmSubmissionRef = useRef<string | null>(null);
  const processedArtifactRef = useRef<string | null>(null);
  const generatedBaseTextRef = useRef("");
  const textRef = useRef("");
  const sourceSettingsTouchedRef = useRef(false);

  const [title, setTitle] = useState("");
  const [sourceType, setSourceType] = useState<SourceType>("journal");
  const [text, setTextState] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [starterMode, setStarterMode] = useState<SourceStarterMode>("exploration_outline");
  const [starterIntent, setStarterIntent] = useState("");
  const [starterRequesting, setStarterRequesting] = useState(false);
  const [starterMutating, setStarterMutating] = useState(false);
  const [starterRefreshing, setStarterRefreshing] = useState(false);
  const [starterRecovering, setStarterRecovering] = useState(true);
  const [starterRecoveryFailed, setStarterRecoveryFailed] = useState(false);
  const [recoveryRetryToken, setRecoveryRetryToken] = useState(0);
  const [pollRetryToken, setPollRetryToken] = useState(0);
  const [starterRun, setStarterRun] = useState<RunView | null>(null);
  const [starterEvents, setStarterEvents] = useState<EventView[]>([]);
  const [candidate, setCandidate] = useState<SourceStarterCandidate | null>(null);
  const [candidateApplied, setCandidateApplied] = useState(false);
  const [starterConfirmed, setStarterConfirmed] = useState(false);
  const [starterError, setStarterError] = useState<unknown>(null);

  function setText(value: string) {
    textRef.current = value;
    setTextState(value);
  }

  function updateTitle(value: string, touched = true) {
    if (touched) sourceSettingsTouchedRef.current = true;
    setTitle(value);
  }

  function updateSourceType(value: SourceType, touched = true) {
    if (touched) sourceSettingsTouchedRef.current = true;
    setSourceType(value);
  }

  const forgetStarter = useCallback(() => {
    requestGenerationRef.current += 1;
    if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
    pollTimerRef.current = null;
    sequenceRef.current = 0;
    processedArtifactRef.current = null;
    generatedBaseTextRef.current = textRef.current;
    starterSubmissionRef.current = null;
    confirmSubmissionRef.current = null;
    setStarterRun(null);
    setStarterEvents([]);
    setCandidate(null);
    setCandidateApplied(false);
    setStarterConfirmed(false);
    setStarterError(null);
  }, []);

  const acceptStarterRun = useCallback((
    nextRun: RunView,
    generation: number,
    options: { recovered?: boolean } = {},
  ) => {
    if (generation !== requestGenerationRef.current) return;
    setStarterRun(nextRun);
    const artifact = sourceStarterArtifact(nextRun);
    const nextCandidate = sourceStarterCandidate(nextRun);
    if (!nextCandidate || !artifact) return;

    if (processedArtifactRef.current === artifact.id) {
      // A refresh can restore the Run before React has restored its candidate
      // state. Re-associate provenance without inserting the text twice.
      setCandidate(nextCandidate);
      return;
    }

    const currentText = textRef.current;
    if (options.recovered && !sourceSettingsTouchedRef.current) {
      if (nextCandidate.source_title) updateTitle(nextCandidate.source_title, false);
      updateSourceType(nextCandidate.source_type, false);
    }
    if (options.recovered && currentText.trim()) {
      // A slow recovery response must never overwrite text entered after the
      // page opened. The candidate remains inspectable and can be deliberately
      // inserted by clearing the current draft first.
      generatedBaseTextRef.current = currentText;
      setCandidateApplied(false);
      setNotice(
        "发现一份尚未确认的 AI 候选。为避免覆盖你刚输入的正文，本次没有自动插入；刷新前未导入的本地修改无法从服务器恢复。",
      );
    } else {
      generatedBaseTextRef.current = currentText;
      setText(appendSourceStarter(currentText, nextCandidate.starter_text));
      setCandidateApplied(true);
      if (options.recovered) {
        setNotice(
          "已从 Run Artifact 恢复上次的 AI 候选。刷新前尚未导入的本地修改没有保存在服务器，请重新核对后再导入。",
        );
      }
    }
    processedArtifactRef.current = artifact.id;
    setCandidate(nextCandidate);
    setStarterConfirmed(false);
    confirmSubmissionRef.current = null;
  }, []);

  const mergeStarterEvents = useCallback((incomingEvents: EventView[]) => {
    setStarterEvents((current) => {
      const merged = mergeEvents(current, incomingEvents);
      sequenceRef.current = lastEventSequence(merged);
      return merged;
    });
  }, []);

  useEffect(() => {
    const generation = requestGenerationRef.current;
    let stopped = false;

    async function recover() {
      try {
        setStarterRecoveryFailed(false);
        const project = await projectsApi.get(projectId);
        const summaries = project.runs
          .filter((run) => run.workflow_type === "source-starter")
          .sort(newestFirst)
          .slice(0, 8);
        for (const summary of summaries) {
          const [run, events] = await Promise.all([
            runsApi.get(summary.id),
            runsApi.events(summary.id, 0),
          ]);
          if (stopped || generation !== requestGenerationRef.current) return;
          if (sourceStarterWasConfirmed(run, events)) continue;
          const recoverable =
            run.status === "queued" ||
            run.status === "running" ||
            run.status === "waiting_for_user" ||
            // Compatibility for candidates generated before M5.1 switched to
            // a durable confirmation checkpoint. Failed/cancelled Runs must
            // never reappear after the user explicitly abandons them.
            (run.status === "succeeded" && sourceStarterCandidate(run) !== null);
          if (!recoverable) continue;
          setStarterEvents(events);
          sequenceRef.current = lastEventSequence(events);
          acceptStarterRun(run, generation, { recovered: true });
          setStarterError(starterFailure(run));
          return;
        }
        setStarterError(null);
      } catch (recoveryError) {
        if (!stopped && generation === requestGenerationRef.current) {
          setStarterRecoveryFailed(true);
          setStarterError(
            recoveryError instanceof Error
              ? new Error(`未能检查上次未确认的 AI 起步 Run：${recoveryError.message}`)
              : recoveryError,
          );
        }
      } finally {
        if (!stopped && generation === requestGenerationRef.current) {
          setStarterRecovering(false);
        }
      }
    }

    void recover();
    return () => { stopped = true; };
  }, [acceptStarterRun, projectId, recoveryRetryToken]);

  useEffect(() => {
    if (!starterRun || pollingPausedStatuses.has(starterRun.status)) return;
    const generation = requestGenerationRef.current;
    let stopped = false;

    const poll = async () => {
      try {
        const [nextRun, incomingEvents] = await Promise.all([
          runsApi.get(starterRun.id),
          runsApi.events(starterRun.id, sequenceRef.current),
        ]);
        if (stopped || generation !== requestGenerationRef.current) return;
        mergeStarterEvents(incomingEvents);
        acceptStarterRun(nextRun, generation);
        setStarterError(starterFailure(nextRun));
        if (!pollingPausedStatuses.has(nextRun.status)) {
          pollTimerRef.current = window.setTimeout(poll, 900);
        }
      } catch (pollError) {
        // Stop here. The visible retry action performs GET only; it never
        // repeats the create POST or spends another model call.
        if (!stopped && generation === requestGenerationRef.current) {
          setStarterError(pollError);
        }
      }
    };

    pollTimerRef.current = window.setTimeout(poll, 250);
    return () => {
      stopped = true;
      if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    };
  }, [acceptStarterRun, mergeStarterEvents, pollRetryToken, starterRun?.id, starterRun?.status]);

  useEffect(() => () => {
    requestGenerationRef.current += 1;
    if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
  }, []);

  async function retryCurrentRun() {
    if (!starterRun) return;
    const generation = requestGenerationRef.current;
    setStarterRefreshing(true);
    try {
      const [nextRun, events] = await Promise.all([
        runsApi.get(starterRun.id),
        runsApi.events(starterRun.id, sequenceRef.current),
      ]);
      if (generation !== requestGenerationRef.current) return;
      mergeStarterEvents(events);
      acceptStarterRun(nextRun, generation);
      setStarterError(starterFailure(nextRun));
      setPollRetryToken((value) => value + 1);
    } catch (refreshError) {
      if (generation === requestGenerationRef.current) setStarterError(refreshError);
    } finally {
      if (generation === requestGenerationRef.current) setStarterRefreshing(false);
    }
  }

  function retryRecovery() {
    setStarterError(null);
    setStarterRecoveryFailed(false);
    setStarterRecovering(true);
    setRecoveryRetryToken((value) => value + 1);
  }

  async function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    updateTitle(file.name.replace(/\.(md|markdown|txt)$/i, ""));
    setText(await file.text());
    setNotice(`已读取 ${file.name}，确认后才会导入。`);
  }

  async function beginStarterGeneration() {
    if (!isStarterSourceType(sourceType)) return;
    const request = {
      source_title: title.trim() || null,
      source_type: sourceType,
      mode: starterMode,
      intent: starterIntent.trim() || null,
    } as const;
    if (!starterSubmissionRef.current) {
      starterSubmissionRef.current = submissionId("ui-source-starter");
    }
    const generation = requestGenerationRef.current + 1;
    requestGenerationRef.current = generation;
    generatedBaseTextRef.current = textRef.current;
    sequenceRef.current = 0;
    processedArtifactRef.current = null;
    setCandidate(null);
    setCandidateApplied(false);
    setStarterRequesting(true);
    setStarterError(null);
    setError(null);
    setNotice("");
    setStarterConfirmed(false);
    setStarterEvents([]);
    try {
      const nextRun = await projectsApi.createSourceStarter(projectId, {
        submission_id: starterSubmissionRef.current,
        ...request,
      });
      if (generation !== requestGenerationRef.current) return;
      acceptStarterRun(nextRun, generation);
      const initialEvents = await runsApi.events(nextRun.id, 0);
      if (generation !== requestGenerationRef.current) return;
      setStarterEvents(initialEvents);
      sequenceRef.current = lastEventSequence(initialEvents);
      setStarterError(starterFailure(nextRun));
    } catch (generateError) {
      if (generation === requestGenerationRef.current) setStarterError(generateError);
    } finally {
      if (generation === requestGenerationRef.current) setStarterRequesting(false);
    }
  }

  async function cancelStarterRunWithRecovery(run: RunView): Promise<RunView> {
    if (terminalRunStatuses.has(run.status)) return run;
    try {
      return await runsApi.cancel(run.id);
    } catch (cancelError) {
      // The POST may have reached the backend even when its response was lost.
      // Reconcile with the durable Run before surfacing an error. This GET can
      // never create a second Run or spend another model call.
      try {
        const recovered = await runsApi.get(run.id);
        if (recovered.status === "cancelled") return recovered;
      } catch {
        // Keep the original cancellation error because it carries the request
        // ID that best identifies the uncertain mutation in backend logs.
      }
      throw cancelError;
    }
  }

  async function regenerateStarter() {
    if (!candidate || !starterRun) {
      await beginStarterGeneration();
      return;
    }
    if (!candidateApplied) {
      setNotice("这份恢复候选尚未插入正文。请先放弃当前 Run，再明确发起新一轮生成。");
      return;
    }
    const removable = removeUneditedSourceStarter(
      textRef.current,
      generatedBaseTextRef.current,
      candidate.starter_text,
    );
    if (removable === null) {
      setNotice(
        "你已经编辑过这份 AI 候选。为避免混入多版内容，请先确认导入；或手动还原后清除当前候选，再重新生成。",
      );
      return;
    }
    if (starterMutationRef.current) return;
    starterMutationRef.current = true;
    setStarterMutating(true);
    try {
      await cancelStarterRunWithRecovery(starterRun);
      setText(removable);
      forgetStarter();
      await beginStarterGeneration();
    } catch (cancelError) {
      setStarterError(cancelError);
    } finally {
      starterMutationRef.current = false;
      setStarterMutating(false);
    }
  }

  async function clearStarter() {
    if (!candidate || !starterRun) {
      forgetStarter();
      return;
    }
    let preservedText = textRef.current;
    if (candidateApplied) {
      const removable = removeUneditedSourceStarter(
        textRef.current,
        generatedBaseTextRef.current,
        candidate.starter_text,
      );
      if (removable === null) {
        setNotice(
          "AI 候选已经被你修改。为避免误删真实内容，不能自动清除；请先确认导入，或手动还原候选文字后再清除。",
        );
        return;
      }
      preservedText = removable;
    }
    if (starterMutationRef.current) return;
    starterMutationRef.current = true;
    setStarterMutating(true);
    try {
      await cancelStarterRunWithRecovery(starterRun);
      setText(preservedText);
      forgetStarter();
      setNotice("已放弃当前 AI 起步 Run；你的原有正文仍然保留。重新生成会创建一次新的模型调用。");
    } catch (cancelError) {
      setStarterError(cancelError);
    } finally {
      starterMutationRef.current = false;
      setStarterMutating(false);
    }
  }

  function abandonFailedRun() {
    forgetStarter();
    setNotice("已放弃失败或取消的 Run，正文和表单内容仍然保留。你可以修改后再明确发起新一轮生成。");
  }

  function applyRecoveredCandidate() {
    if (!candidate || candidateApplied) return;
    const currentText = textRef.current;
    generatedBaseTextRef.current = currentText;
    setText(appendSourceStarter(currentText, candidate.starter_text));
    setCandidateApplied(true);
    setStarterConfirmed(false);
    setNotice("已将恢复的候选追加到正文末尾，没有覆盖你刚输入的内容。请编辑核对后再确认导入。");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || !text.trim()) return;
    if (candidate && (!starterConfirmed || !starterRun)) return;
    if (candidate && !isStarterSourceType(sourceType)) return;
    setSubmitting(true);
    setError(null);
    setNotice("");
    try {
      let result: ImportSourceResponse;
      if (candidate && starterRun) {
        if (!isStarterSourceType(sourceType)) return;
        if (!confirmSubmissionRef.current) {
          confirmSubmissionRef.current = submissionId("ui-source-starter-confirm");
        }
        result = await projectsApi.confirmSourceStarter(projectId, starterRun.id, {
          submission_id: confirmSubmissionRef.current,
          title: title.trim(),
          source_type: sourceType,
          text,
        });
      } else {
        result = await projectsApi.importSource(projectId, {
          title: title.trim(),
          source_type: sourceType,
          text,
        });
      }
      setNotice(
        result.created
          ? `已导入并切分为 ${result.source.segment_count} 个片段。`
          : result.linked
            ? "相同内容已经存在；已关联到当前 Project。"
            : "相同内容已经在当前 Project 中，没有创建副本。",
      );
      updateTitle("", false);
      updateSourceType("journal", false);
      sourceSettingsTouchedRef.current = false;
      setText("");
      setStarterIntent("");
      forgetStarter();
      if (fileRef.current) fileRef.current.value = "";
      onImported(result);
    } catch (submitError) {
      setError(submitError);
    } finally {
      setSubmitting(false);
    }
  }

  const starterAllowed = isStarterSourceType(sourceType);
  const starterBusy =
    starterRecovering ||
    starterRecoveryFailed ||
    starterRequesting ||
    starterMutating ||
    starterRefreshing ||
    (starterRun !== null && ["queued", "running"].includes(starterRun.status));
  const runFailedOrCancelled =
    starterRun !== null && ["failed", "cancelled"].includes(starterRun.status);
  const persistedConfirmation = sourceStarterWasConfirmed(starterRun, starterEvents);
  const steps = sourceStarterSteps(
    starterRun,
    starterEvents,
    candidate,
    persistedConfirmation,
  );
  const importDisabled =
    submitting ||
    starterBusy ||
    !title.trim() ||
    !text.trim() ||
    (candidate !== null && (!candidateApplied || !starterConfirmed || !starterAllowed));
  const canRetryGet =
    starterRun !== null &&
    !pollingPausedStatuses.has(starterRun.status) &&
    starterError !== null;
  const canRetryRead = canRetryGet || starterRecoveryFailed;
  const fileLocked = starterBusy || starterRun !== null;

  return (
    <form className="source-importer" onSubmit={submit}>
      <div className="form-row two-columns">
        <label>
          标题
          <input
            value={title}
            onChange={(event) => updateTitle(event.target.value)}
            placeholder="这份素材是什么？"
            required
          />
        </label>
        <label>
          类型
          <select
            value={sourceType}
            disabled={starterBusy || starterRun !== null}
            onChange={(event) => {
              updateSourceType(event.target.value as SourceType);
              setStarterConfirmed(false);
            }}
          >
            <option value="journal">日记 / 随想</option>
            <option value="voice_note_transcript">口述转写</option>
            <option value="podcast_draft">播客旧稿</option>
            <option value="writing_sample">写作样本</option>
            <option value="other">其他</option>
          </select>
        </label>
      </div>

      {starterAllowed || candidate || starterRun ? (
        <section className="ai-starter-panel" aria-labelledby="ai-starter-title">
          <div className="ai-starter-heading">
            <div>
              <span className="ai-chip">AI 起步助手</span>
              <h3 id="ai-starter-title">最难的往往只是第一笔</h3>
              <p>使用 Project 上下文生成可编辑候选稿，不会自动保存成素材。</p>
            </div>
            {starterAllowed && !runFailedOrCancelled && (
              <button
                type="button"
                className="button secondary"
                disabled={starterBusy}
                onClick={() => {
                  if (candidate) void regenerateStarter();
                  else if (!starterRun) void beginStarterGeneration();
                }}
              >
                {starterBusy
                  ? starterRecovering
                    ? "正在恢复…"
                    : starterMutating
                      ? "正在处理…"
                      : "正在生成…"
                  : candidate ? "✨ 重新生成（新调用）" : "✨ 帮我起个头"}
              </button>
            )}
          </div>
          {starterAllowed ? (
            <div className="form-row two-columns ai-starter-settings">
              <label>
                起步方式
                <select
                  value={starterMode}
                  disabled={starterBusy || starterRun !== null}
                  onChange={(event) => setStarterMode(event.target.value as SourceStarterMode)}
                >
                  <option value="exploration_outline">探索提纲（推荐）</option>
                  <option value="starter_draft">示例草稿</option>
                </select>
              </label>
              <label>
                特别想探索什么 <span className="optional">可选</span>
                <input
                  value={starterIntent}
                  disabled={starterBusy || starterRun !== null}
                  onChange={(event) => setStarterIntent(event.target.value)}
                  placeholder="例如：我为什么会被潜水吸引？"
                />
              </label>
            </div>
          ) : (
            <p className="ai-starter-boundary">
              这份 AI 草稿不能导入为
              {sourceType === "writing_sample" ? "写作样本" : "口述转写"}。
              请切回日记、播客旧稿或其他类型，或者清除 AI 草稿。
            </p>
          )}

          {starterRun && (
            <div className="ai-starter-progress" aria-label="AI 起步 Run 步骤">
              <ol>
                {steps.map((step) => (
                  <li key={step.key} className={`starter-step starter-step-${step.status}`}>
                    <span aria-hidden="true" />
                    <div><strong>{step.label}</strong><small>{step.detail}</small></div>
                  </li>
                ))}
              </ol>
              <Link className="text-button" to={`/runs/${starterRun.id}`}>查看完整 Run trace</Link>
            </div>
          )}

          {candidate && (
            <div className="ai-draft-notice">
              <div>
                <strong>AI 草稿 · 尚未保存为素材</strong>
                <p>
                  {candidateApplied
                    ? "已经写入下方正文。请核对事实、删除不符合你的内容，并补上真实经历。"
                    : "为保护已有正文，本次没有自动插入。你可以在下方展开候选内容并手动取用。"}
                </p>
              </div>
              <button type="button" className="text-button" onClick={() => { void clearStarter(); }}>
                放弃当前 Run
              </button>
              {!candidateApplied && (
                <details open>
                  <summary>查看恢复的 AI 候选</summary>
                  <p className="ai-candidate-preview">{candidate.starter_text}</p>
                  <button type="button" className="button secondary small" onClick={applyRecoveredCandidate}>
                    追加候选到正文
                  </button>
                </details>
              )}
              {(candidate.questions.length > 0 || candidate.uncertainties.length > 0) && (
                <details>
                  <summary>查看继续思考的提示</summary>
                  {candidate.questions.length > 0 && (
                    <ul>{candidate.questions.map((question) => <li key={question}>{question}</li>)}</ul>
                  )}
                  {candidate.uncertainties.length > 0 && (
                    <p>仍需你确认：{candidate.uncertainties.join("；")}</p>
                  )}
                </details>
              )}
            </div>
          )}
          {runFailedOrCancelled && !candidate && (
            <button type="button" className="text-button abandon-run" onClick={abandonFailedRun}>
              放弃本次 Run，保留正文
            </button>
          )}
          <ErrorNotice
            error={starterError}
            onRetry={
              canRetryGet
                ? () => { void retryCurrentRun(); }
                : starterRecoveryFailed ? retryRecovery : undefined
            }
          />
          {Boolean(starterError) && canRetryRead && (
            <p className="retry-explanation">
              重试只读取当前 Project / Run，不会再次创建 Run 或产生新的模型调用。
            </p>
          )}
        </section>
      ) : (
        <p className="ai-starter-boundary">
          {sourceType === "writing_sample"
            ? "写作样本必须来自你本人，AI 生成内容不能冒充个人风格样本。"
            : "口述转写必须来自真实口述，AI 生成内容不能冒充你的录音。"}
        </p>
      )}

      <label>
        正文
        <textarea
          className="source-textarea"
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            if (candidate) setStarterConfirmed(false);
          }}
          placeholder="直接粘贴日记、旧稿或已经转成文字的口述。这里不会直接接收音频。"
          rows={10}
          required
        />
      </label>

      {candidate && candidateApplied && (
        <label className="check-row ai-confirmation">
          <input
            type="checkbox"
            checked={starterConfirmed}
            onChange={(event) => setStarterConfirmed(event.target.checked)}
          />
          <span>我已检查这份 AI 草稿，并确认其中的事实和个人经历后再导入。</span>
        </label>
      )}

      <div className="form-actions split-actions">
        <div>
          <input
            ref={fileRef}
            className="visually-hidden"
            id="source-file"
            type="file"
            accept=".txt,.md,.markdown,text/plain,text/markdown"
            disabled={fileLocked}
            onChange={chooseFile}
          />
          <label
            className={`button ghost${fileLocked ? " disabled" : ""}`}
            htmlFor="source-file"
            aria-disabled={fileLocked}
          >
            选择 TXT / Markdown
          </label>
        </div>
        <button className="button primary" disabled={importDisabled}>
          {submitting ? "正在导入…" : candidate ? "确认并导入 Source" : "导入 Source"}
        </button>
      </div>
      {notice && <p className="form-notice" role="status">{notice}</p>}
      <ErrorNotice error={error} />
    </form>
  );
}
