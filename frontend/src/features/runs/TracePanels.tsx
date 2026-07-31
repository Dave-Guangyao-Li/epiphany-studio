import { useMemo, useState } from "react";
import type { ArtifactView, EventView, ModelCallView, TaskView } from "../../api/types";
import { StatusBadge } from "../../components/StatusBadge";

function shortId(value: string) {
  return value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-5)}` : value;
}

function time(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function eventLabel(type: string) {
  const labels: Record<string, string> = {
    "run.created": "创建 Run",
    "run.started": "开始执行",
    "run.waiting_for_user": "等待你的补充",
    "run.resumed": "收到补充并继续",
    "run.succeeded": "Run 完成",
    "run.failed": "Run 失败",
    "run.cancelled": "Run 已取消",
    "workflow.fan_out.started": "两位 Researcher 并行出发",
    "workflow.fan_in.waiting": "等待研究结果汇合",
    "workflow.fan_in.completed": "研究结果汇合",
    "workflow.interview_scaffold.completed": "采访脚手架完成",
    "workflow.material_readiness.evaluated": "素材充分度已评估",
    "workflow.user_input.requested": "请求补充口述",
    "workflow.editor.completed": "口播稿已生成",
    "workflow.draft_quality.completed": "质量评估完成",
    "workflow.draft_supplemental_interview.completed": "生成针对性补充问题",
  };
  return labels[type] ?? type.replaceAll(".", " · ");
}

export function EventTimeline({ events }: { events: EventView[] }) {
  if (!events.length) return <p className="muted">事件正在写入数据库…</p>;
  return (
    <ol className="event-timeline">
      {events.map((event) => (
        <li key={event.sequence} className={event.type.includes("failed") ? "danger" : ""}>
          <span className="event-sequence">{event.sequence}</span>
          <div>
            <div className="event-title"><strong>{eventLabel(event.type)}</strong><time>{time(event.created_at)}</time></div>
            {event.task_id && <code title={event.task_id}>{shortId(event.task_id)}</code>}
            {Object.keys(event.payload).length > 0 && (
              <details><summary>查看事件数据</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function TaskList({ tasks }: { tasks: TaskView[] }) {
  return (
    <div className="task-list">
      {tasks.map((task) => (
        <article className="task-card" key={task.id}>
          <div className="task-card-heading"><StatusBadge status={task.status} /><code title={task.id}>{shortId(task.id)}</code></div>
          <h4>{task.kind.replaceAll("_", " ")}</h4>
          <p>{task.agent_type} · attempt {task.attempt}/{task.max_attempts}</p>
          {task.error_code && (
            <div className="inline-error"><strong>{task.error_code}</strong><span>{task.error_message}</span></div>
          )}
        </article>
      ))}
    </div>
  );
}

function currencyAmount(micros: number, currency: string) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    maximumFractionDigits: 6,
  }).format(micros / 1_000_000);
}

export function ModelCallTable({ calls }: { calls: ModelCallView[] }) {
  const totals = useMemo(() => {
    const grouped = new Map<string, number>();
    for (const call of calls) {
      grouped.set(call.cost_currency, (grouped.get(call.cost_currency) ?? 0) + call.estimated_cost_micros);
    }
    return [...grouped.entries()];
  }, [calls]);

  return (
    <div>
      <div className="cost-summary">
        {totals.length ? totals.map(([currency, micros]) => (
          <span key={currency}><small>{currency} 本地估算</small><strong>{currencyAmount(micros, currency)}</strong></span>
        )) : <span><small>尚无模型调用</small><strong>—</strong></span>}
      </div>
      {calls.length > 0 && (
        <div className="source-table-wrap">
          <table className="data-table model-table">
            <thead><tr><th>模型</th><th>状态</th><th>Tokens</th><th>耗时</th><th>费用</th></tr></thead>
            <tbody>{calls.map((call) => (
              <tr key={call.id}>
                <td><strong>{call.model}</strong><small>{call.provider} · #{call.attempt}</small></td>
                <td><StatusBadge status={call.status} />{call.error_code && <small>{call.error_code}</small>}</td>
                <td>{(call.input_tokens + call.output_tokens).toLocaleString()}<small>{call.input_tokens} in / {call.output_tokens} out</small></td>
                <td>{call.duration_ms == null ? "—" : `${(call.duration_ms / 1000).toFixed(2)}s`}</td>
                <td>{currencyAmount(call.estimated_cost_micros, call.cost_currency)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function ArtifactViewer({ artifacts }: { artifacts: ArtifactView[] }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = artifacts.find((artifact) => artifact.id === selectedId) ?? artifacts.at(-1);
  if (!artifacts.length) return <p className="muted">Agent 完成任务后，产物会出现在这里。</p>;
  return (
    <div className="artifact-browser">
      <div className="artifact-tabs" role="tablist" aria-label="运行产物">
        {artifacts.map((artifact) => (
          <button
            role="tab"
            aria-selected={artifact.id === selected?.id}
            className={artifact.id === selected?.id ? "active" : ""}
            key={artifact.id}
            onClick={() => setSelectedId(artifact.id)}
          >
            {artifact.kind.replaceAll("_", " ")}
          </button>
        ))}
      </div>
      {selected && (
        <div className="artifact-content">
          <div><strong>{selected.kind}</strong><code>{shortId(selected.id)}</code></div>
          <details>
            <summary>查看结构化 Artifact</summary>
            <pre>{JSON.stringify(selected.content_json, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  );
}
