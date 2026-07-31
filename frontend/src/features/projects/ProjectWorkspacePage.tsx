import { useCallback, useEffect, useState } from "react";
import { projectsApi, sourcesApi } from "../../api/epiphany";
import type { ProjectDetail, SourceDetail } from "../../api/types";
import { Link, useParams } from "../../app/router";
import { EmptyState } from "../../components/EmptyState";
import { ErrorNotice } from "../../components/ErrorNotice";
import { StatusBadge } from "../../components/StatusBadge";
import { CreateRunForm } from "../runs/CreateRunForm";
import { SourceImporter } from "../sources/SourceImporter";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function ProjectWorkspacePage() {
  const { projectId = "" } = useParams();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [selectedSource, setSelectedSource] = useState<SourceDetail | null>(null);
  const [showImporter, setShowImporter] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setProject(await projectsApi.get(projectId));
    } catch (loadError) {
      setError(loadError);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);

  async function openSource(sourceId: string) {
    setError(null);
    try {
      setSelectedSource(await sourcesApi.get(sourceId));
    } catch (sourceError) {
      setError(sourceError);
    }
  }

  if (loading && !project) {
    return <div className="page"><div className="loading-line">正在打开 Project…</div></div>;
  }

  return (
    <div className="page project-workspace">
      <ErrorNotice error={error} onRetry={load} />
      {project && (
        <>
          <header className="workspace-header">
            <div>
              <Link className="back-link" to="/projects">← 所有 Projects</Link>
              <p className="eyebrow">PROJECT WORKSPACE</p>
              <h1>{project.title}</h1>
              <p>{project.description || "把相关素材放在一起，再从一个具体问题开始。"}</p>
            </div>
            <dl className="workspace-stats">
              <div><dt>Sources</dt><dd>{project.source_count}</dd></div>
              <div><dt>Runs</dt><dd>{project.run_count}</dd></div>
            </dl>
          </header>

          <div className="workspace-grid">
            <section className="source-library panel">
              <div className="panel-heading">
                <div><p className="eyebrow">EVIDENCE</p><h2>素材库</h2></div>
                <button className="button secondary small" onClick={() => setShowImporter((value) => !value)}>
                  {showImporter ? "收起" : "+ 添加素材"}
                </button>
              </div>

              {showImporter && (
                <SourceImporter
                  projectId={project.id}
                  onImported={() => { void load(); }}
                />
              )}

              {project.sources.length === 0 ? (
                <EmptyState title="先放进一份真实素材">
                  可以粘贴日记、旧播客稿或口述转写。系统会保留每个段落的来源。
                </EmptyState>
              ) : (
                <div className="source-table-wrap">
                  <table className="data-table source-table">
                    <thead><tr><th>Source</th><th>类型</th><th>规模</th><th /></tr></thead>
                    <tbody>
                      {project.sources.map((source) => (
                        <tr key={source.id}>
                          <td><strong>{source.title}</strong><small>{formatDate(source.created_at)}</small></td>
                          <td><span className="type-chip">{source.source_type}</span></td>
                          <td>{source.char_count.toLocaleString()} 字<br /><small>{source.segment_count} segments</small></td>
                          <td><button className="text-button" onClick={() => { void openSource(source.id); }}>查看</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {selectedSource && (
                <aside className="source-detail" aria-label="Source 详情">
                  <div className="panel-heading">
                    <div><p className="eyebrow">SOURCE DETAIL</p><h3>{selectedSource.title}</h3></div>
                    <button className="icon-button" aria-label="关闭" onClick={() => setSelectedSource(null)}>×</button>
                  </div>
                  <p className="source-detail-meta">
                    {selectedSource.char_count.toLocaleString()} 字 · {selectedSource.segments.length} 个稳定片段
                  </p>
                  <div className="segment-list">
                    {selectedSource.segments.map((segment) => (
                      <article key={segment.id}>
                        <span>段落 {segment.position + 1}</span>
                        <p>{segment.text}</p>
                      </article>
                    ))}
                  </div>
                </aside>
              )}
            </section>

            <aside className="builder-panel panel">
              {project.sources.length ? (
                <CreateRunForm projectId={project.id} sources={project.sources} />
              ) : (
                <EmptyState title="还不能启动 Run">导入至少一份事实素材后，这里会出现创作配置。</EmptyState>
              )}
            </aside>
          </div>

          <section className="section-block run-history">
            <div className="section-heading">
              <div><p className="eyebrow">IMMUTABLE HISTORY</p><h2>Run 历史</h2></div>
              <span className="quiet-count">{project.runs.length} 次</span>
            </div>
            {project.runs.length === 0 ? (
              <EmptyState title="还没有运行记录">创建一次 Run 后，Agent 的每一步都会在这里留下可回放轨迹。</EmptyState>
            ) : (
              <div className="run-list">
                {project.runs.map((run) => (
                  <Link to={`/runs/${run.id}`} className="run-row" key={run.id}>
                    <div><StatusBadge status={run.status} /><strong>{run.current_step || run.workflow_type}</strong></div>
                    <div><span>{run.workflow_version}</span><span>{run.model_call_count} calls</span><span>{formatDate(run.created_at)}</span><b>→</b></div>
                  </Link>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
