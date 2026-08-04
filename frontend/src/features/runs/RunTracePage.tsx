import { useCallback, useEffect, useRef, useState } from "react";
import { apiEventStreamUrl } from "../../api/client";
import { runsApi } from "../../api/epiphany";
import type {
  EventView,
  ImprovementPlanRecord,
  QualityReportRecord,
  RunView,
  SupplementalInterviewPlanRecord,
  TaskView,
} from "../../api/types";
import { Link, useParams } from "../../app/router";
import { ErrorNotice } from "../../components/ErrorNotice";
import { StatusBadge } from "../../components/StatusBadge";
import { DURABLE_EVENT_NAMES, lastEventSequence, mergeEvents } from "../../lib/events";
import {
  latestMaterialReadiness,
  isCurrentRunGeneration,
  loadSupplementalInterviewForRun,
  runMarkdownAvailability,
  type RunMarkdownKind,
  type RunRouteGeneration,
  shouldLoadDerivedForRun,
} from "../../lib/runTrace";
import {
  FeedbackPanel,
  HumanCheckpointPanel,
  ImprovementAnswerPanel,
  RevisionPanel,
  SupplementalInterviewPanel,
} from "./RunActions";
import { ArtifactViewer, EventTimeline, ModelCallTable, TaskList } from "./TracePanels";

type ConnectionState = "connecting" | "live" | "reconnecting" | "closed";

const terminalStatuses = new Set(["succeeded", "failed", "cancelled"]);

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "medium" }).format(new Date(value));
}

export function commitForCurrentRunRoute(
  requested: RunRouteGeneration,
  current: RunRouteGeneration,
  commit: () => void,
): boolean {
  if (!isCurrentRunGeneration(requested, current)) return false;
  commit();
  return true;
}

function isQualityReviewerTask(task: TaskView): boolean {
  return task.kind === "review_podcast_draft" || task.agent_type === "quality_reviewer";
}

function qualityReviewIsUnavailable(quality: QualityReportRecord | null): boolean {
  return quality?.report.model_review_status === "unavailable" ||
    quality?.report.decision === "automated_review_incomplete";
}

export function partitionRunTaskErrors(
  run: RunView,
  quality: QualityReportRecord | null,
): { blocking: TaskView[]; advisoryReview: TaskView[]; showReviewWarning: boolean } {
  const errored = run.tasks.filter((task) => task.error_code);
  if (run.status !== "succeeded") {
    return { blocking: errored, advisoryReview: [], showReviewWarning: false };
  }

  const advisoryReview = errored.filter(isQualityReviewerTask);
  return {
    blocking: errored.filter((task) => !isQualityReviewerTask(task)),
    advisoryReview,
    // A succeeded Run has already preserved a valid Editor Draft. Recognize a
    // failed advisory Reviewer immediately, before the derived report finishes
    // loading, so the page never flashes a false fatal-error banner.
    showReviewWarning: advisoryReview.length > 0 || qualityReviewIsUnavailable(quality),
  };
}

export function RunTaskNotices({
  run,
  quality,
}: {
  run: RunView;
  quality: QualityReportRecord | null;
}) {
  const { blocking, advisoryReview, showReviewWarning } = partitionRunTaskErrors(run, quality);
  return (
    <>
      {blocking.length > 0 && (
        <section className="run-failure" role="alert">
          <strong>执行错误</strong>
          {blocking.map((task) => <p key={task.id}><code>{task.error_code}</code> · {task.error_message} · Task <code>{task.id}</code></p>)}
        </section>
      )}
      {showReviewWarning && (
        <section className="run-warning" role="status">
          <strong>自动质量审阅未完成</strong>
          <p>口播稿已保留，Run 已正常完成。确定性质量检查仍然有效；发布前请人工检查稿件。</p>
          {advisoryReview.length > 0 && (
            <details>
              <summary>查看审阅失败详情</summary>
              {advisoryReview.map((task) => <p key={task.id}><code>{task.error_code}</code> · Task <code>{task.id}</code></p>)}
            </details>
          )}
        </section>
      )}
    </>
  );
}

export function RunTracePage() {
  const { runId = "" } = useParams();
  const sequenceRef = useRef(0);
  const eventStreamRef = useRef<EventSource | null>(null);
  const refreshInFlightRef = useRef<{
    route: RunRouteGeneration;
    promise: Promise<void>;
  } | null>(null);
  const refreshTimerRef = useRef<number | null>(null);
  const derivedRequestedRunRef = useRef<string | null>(null);
  const routeGenerationRef = useRef(0);
  const activeRunIdRef = useRef(runId);
  activeRunIdRef.current = runId;
  const [run, setRun] = useState<RunView | null>(null);
  const [events, setEvents] = useState<EventView[]>([]);
  const [quality, setQuality] = useState<QualityReportRecord | null>(null);
  const [improvement, setImprovement] = useState<ImprovementPlanRecord | null>(null);
  const [supplemental, setSupplemental] = useState<SupplementalInterviewPlanRecord | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<unknown>(null);
  const [cancelling, setCancelling] = useState(false);
  const [activeView, setActiveView] = useState<"trace" | "tasks" | "artifacts">("trace");
  const [markdown, setMarkdown] = useState<{ kind: RunMarkdownKind; text: string } | null>(null);
  const [markdownError, setMarkdownError] = useState<unknown>(null);
  const [initialReplayComplete, setInitialReplayComplete] = useState(false);

  const currentRouteGeneration = useCallback((): RunRouteGeneration => ({
    runId: activeRunIdRef.current,
    generation: routeGenerationRef.current,
  }), []);

  const loadDerived = useCallback(async (
    nextRun: RunView,
    requestedRoute: RunRouteGeneration,
  ) => {
    if (!shouldLoadDerivedForRun(nextRun, derivedRequestedRunRef.current)) return;
    derivedRequestedRunRef.current = nextRun.id;
    try {
      const [qualityResult, improvementResult, supplementalResult] = await Promise.all([
        runsApi.quality(nextRun.id),
        runsApi.improvement(nextRun.id),
        loadSupplementalInterviewForRun(nextRun, runsApi.supplemental),
      ]);
      if (!isCurrentRunGeneration(requestedRoute, currentRouteGeneration())) return;
      setQuality(qualityResult);
      setImprovement(improvementResult);
      setSupplemental(supplementalResult);
    } catch (loadError) {
      // A transient failure must remain retryable through the page retry action.
      if (derivedRequestedRunRef.current === nextRun.id) {
        derivedRequestedRunRef.current = null;
      }
      throw loadError;
    }
  }, [currentRouteGeneration]);

  const refresh = useCallback((generation = routeGenerationRef.current): Promise<void> => {
    const requestedRoute = { runId, generation };
    if (
      refreshInFlightRef.current &&
      isCurrentRunGeneration(refreshInFlightRef.current.route, requestedRoute)
    ) return refreshInFlightRef.current.promise;
    const request = (async () => {
      setError(null);
      try {
        const [nextRun, replay] = await Promise.all([
          runsApi.get(runId),
          runsApi.events(runId, sequenceRef.current),
        ]);
        if (!isCurrentRunGeneration(requestedRoute, currentRouteGeneration())) return;
        if (replay.length) {
          setEvents((current) => {
            const merged = mergeEvents(current, replay);
            sequenceRef.current = lastEventSequence(merged);
            return merged;
          });
        }
        setRun(nextRun);
        await loadDerived(nextRun, requestedRoute);
      } catch (loadError) {
        if (!isCurrentRunGeneration(requestedRoute, currentRouteGeneration())) return;
        setError(loadError);
      }
    })().finally(() => {
      if (refreshInFlightRef.current?.promise === request) refreshInFlightRef.current = null;
    });
    refreshInFlightRef.current = { route: requestedRoute, promise: request };
    return request;
  }, [currentRouteGeneration, loadDerived, runId]);

  const scheduleRefresh = useCallback((delay = 180) => {
    if (refreshTimerRef.current !== null) window.clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = window.setTimeout(() => {
      refreshTimerRef.current = null;
      void refresh();
    }, delay);
  }, [refresh]);
  const runIsTerminal = run !== null && terminalStatuses.has(run.status);

  useEffect(() => {
    routeGenerationRef.current += 1;
    const generation = routeGenerationRef.current;
    sequenceRef.current = 0;
    derivedRequestedRunRef.current = null;
    setEvents([]);
    setRun(null);
    setQuality(null);
    setImprovement(null);
    setSupplemental(null);
    setCancelling(false);
    setMarkdown(null);
    setMarkdownError(null);
    setInitialReplayComplete(false);
    let current = true;
    void refresh(generation).finally(() => {
      if (current) setInitialReplayComplete(true);
    });
    return () => { current = false; };
  }, [refresh, runId]);

  useEffect(() => {
    if (!initialReplayComplete || runIsTerminal) {
      if (runIsTerminal) setConnection("closed");
      return;
    }
    let stopped = false;
    let stream: EventSource | null = null;
    setConnection("connecting");

    const receive = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as EventView;
        setEvents((current) => {
          const merged = mergeEvents(current, [event]);
          sequenceRef.current = lastEventSequence(merged);
          return merged;
        });
        setConnection("live");
        scheduleRefresh(event.type.startsWith("run.") ? 0 : 180);
      } catch {
        setConnection("reconnecting");
      }
    };

    stream = new EventSource(
      apiEventStreamUrl(`/runs/${runId}/events/stream?after=${sequenceRef.current}`),
    );
    eventStreamRef.current = stream;
    stream.addEventListener("trace", receive as EventListener);
    stream.onmessage = receive;
    for (const eventName of DURABLE_EVENT_NAMES) {
      stream.addEventListener(eventName, receive as EventListener);
    }
    stream.onopen = () => setConnection("live");
    stream.onerror = () => {
      if (!stopped) setConnection("reconnecting");
    };

    const poll = window.setInterval(() => { void refresh(); }, 4_000);
    return () => {
      stopped = true;
      window.clearInterval(poll);
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
      stream?.close();
      if (eventStreamRef.current === stream) eventStreamRef.current = null;
    };
  }, [initialReplayComplete, refresh, runId, runIsTerminal, scheduleRefresh]);

  useEffect(() => {
    if (run && terminalStatuses.has(run.status)) {
      eventStreamRef.current?.close();
      eventStreamRef.current = null;
      setConnection("closed");
    }
  }, [run]);

  async function cancel() {
    const requestedRoute = currentRouteGeneration();
    setCancelling(true);
    setError(null);
    try {
      const cancelledRun = await runsApi.cancel(runId);
      if (!commitForCurrentRunRoute(
        requestedRoute,
        currentRouteGeneration(),
        () => setRun(cancelledRun),
      )) return;
      await refresh(requestedRoute.generation);
    } catch (cancelError) {
      commitForCurrentRunRoute(
        requestedRoute,
        currentRouteGeneration(),
        () => setError(cancelError),
      );
    } finally {
      commitForCurrentRunRoute(
        requestedRoute,
        currentRouteGeneration(),
        () => setCancelling(false),
      );
    }
  }

  async function openMarkdown(kind: RunMarkdownKind) {
    if (!run || !runMarkdownAvailability(run)[kind]) return;
    const requestedRoute = currentRouteGeneration();
    setMarkdownError(null);
    try {
      const loaders = {
        scaffold: runsApi.scaffoldMarkdown,
        draft: runsApi.draftMarkdown,
        "show-notes": runsApi.showNotesMarkdown,
        quality: runsApi.qualityMarkdown,
      };
      const text = await loaders[kind](runId);
      commitForCurrentRunRoute(
        requestedRoute,
        currentRouteGeneration(),
        () => setMarkdown({ kind, text }),
      );
    } catch (loadError) {
      commitForCurrentRunRoute(
        requestedRoute,
        currentRouteGeneration(),
        () => setMarkdownError(loadError),
      );
    }
  }

  if (!run) {
    return <div className="page"><ErrorNotice error={error} onRetry={() => { void refresh(); }} /><div className="loading-line">正在回放 Run Trace…</div></div>;
  }

  const active = !terminalStatuses.has(run.status);
  const readiness = latestMaterialReadiness(run.artifacts);
  const markdownAvailability = runMarkdownAvailability(run);

  return (
    <div className="page run-trace-page">
      <header className="run-header">
        <div>
          <div className="breadcrumb-row">
            {run.project_id && <Link to={`/projects/${run.project_id}`}>← Project</Link>}
            {run.parent_run_id && <Link to={`/runs/${run.parent_run_id}`}>父 Run</Link>}
          </div>
          <p className="eyebrow">DURABLE RUN TRACE · {run.workflow_version}</p>
          <div className="run-title-row"><h1>{run.current_step?.replaceAll("_", " ") || run.workflow_type}</h1><StatusBadge status={run.status} /></div>
          <p className="run-id"><code>{run.id}</code> · 创建于 {formatDate(run.created_at)}</p>
        </div>
        <div className="run-header-actions">
          <span className={`connection-state connection-${connection}`}><i />{connection}</span>
          {active && <button className="button danger" disabled={cancelling} onClick={() => { void cancel(); }}>{cancelling ? "取消中…" : "取消 Run"}</button>}
        </div>
      </header>

      <ErrorNotice error={error} onRetry={() => { void refresh(); }} />
      <RunTaskNotices run={run} quality={quality} />

      <section className="run-overview-grid">
        <article><span>Tasks</span><strong>{run.tasks.length}</strong><small>{run.tasks.filter((task) => task.status === "succeeded").length} succeeded</small></article>
        <article><span>Model calls</span><strong>{run.model_call_count}</strong><small>{run.model_calls.reduce((sum, call) => sum + call.input_tokens + call.output_tokens, 0).toLocaleString()} tokens</small></article>
        <article><span>Artifacts</span><strong>{run.artifacts.length}</strong><small>append-only candidates</small></article>
        <article><span>Events</span><strong>{events.length}</strong><small>last sequence {sequenceRef.current}</small></article>
      </section>

      <nav className="trace-tabs" aria-label="Trace 视图">
        {(["trace", "tasks", "artifacts"] as const).map((view) => (
          <button key={view} className={activeView === view ? "active" : ""} onClick={() => setActiveView(view)}>{view}</button>
        ))}
      </nav>

      <section className="trace-main panel">
        {activeView === "trace" && <EventTimeline events={events} />}
        {activeView === "tasks" && <><TaskList tasks={run.tasks} /><div className="subsection"><h3>模型调用与费用</h3><ModelCallTable calls={run.model_calls} /></div></>}
        {activeView === "artifacts" && <ArtifactViewer artifacts={run.artifacts} />}
      </section>

      <section className="export-strip">
        <div><p className="eyebrow">READABLE OUTPUTS</p><h2>查看生成结果</h2></div>
        <div>
          {markdownAvailability.scaffold && <button className="button ghost" onClick={() => { void openMarkdown("scaffold"); }}>采访脚手架</button>}
          {markdownAvailability.draft && <button className="button ghost" onClick={() => { void openMarkdown("draft"); }}>口播稿</button>}
          {markdownAvailability["show-notes"] && <button className="button ghost" onClick={() => { void openMarkdown("show-notes"); }}>Show Notes</button>}
          {markdownAvailability.quality && <button className="button ghost" onClick={() => { void openMarkdown("quality"); }} disabled={!quality}>质量报告</button>}
        </div>
      </section>
      <ErrorNotice error={markdownError} />

      {markdown && (
        <section className="markdown-viewer panel">
          <div className="panel-heading"><h2>{markdown.kind}</h2><button className="icon-button" onClick={() => setMarkdown(null)}>×</button></div>
          <pre>{markdown.text}</pre>
        </section>
      )}

      {run.status === "waiting_for_user" && (
        <HumanCheckpointPanel run={run} events={events} readiness={readiness} onChanged={refresh} />
      )}

      {run.status === "succeeded" && (
        <div className="post-run-grid">
          <div>
            {quality && (
              <section className="action-card quality-summary">
                <p className="eyebrow">QUALITY REPORT</p><h3>确定性证据 + 模型编辑建议</h3>
                <pre>{JSON.stringify(quality.report, null, 2)}</pre>
              </section>
            )}
            <FeedbackPanel runId={run.id} />
          </div>
          <div>
            <ImprovementAnswerPanel run={run} improvement={improvement} />
            {supplemental && <SupplementalInterviewPanel run={run} record={supplemental} />}
            <RevisionPanel run={run} improvement={improvement} />
          </div>
        </div>
      )}
    </div>
  );
}
