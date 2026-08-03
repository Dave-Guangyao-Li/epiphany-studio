import { type FormEvent, useCallback, useEffect, useState } from "react";
import { projectsApi } from "../../api/epiphany";
import type { ProjectSummary } from "../../api/types";
import { Link, useNavigate } from "../../app/router";
import { EmptyState } from "../../components/EmptyState";
import { ErrorNotice } from "../../components/ErrorNotice";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium" }).format(new Date(value));
}

export function ProjectsPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setProjects(await projectsApi.list());
    } catch (loadError) {
      setError(loadError);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function createProject(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const project = await projectsApi.create(title.trim(), description);
      navigate(`/projects/${project.id}`);
    } catch (createError) {
      setError(createError);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="page page-projects">
      <section className="hero compact-hero">
        <div>
          <p className="eyebrow">LOCAL CREATIVE WORKSPACE</p>
          <h1>把零散生活，整理成可以开口讲的故事。</h1>
          <p className="hero-copy">
            一个 Project 保存同一主题的素材、创作意图和每一次不可变的 Run。
          </p>
        </div>
        <form className="new-project-card" onSubmit={createProject}>
          <div className="section-kicker">新建 Project</div>
          <label>
            名称
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="比如：成年十年第一季"
              maxLength={200}
              required
            />
          </label>
          <label>
            一句话说明 <span className="optional">可选</span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="我为什么想做这个主题？"
              rows={3}
            />
          </label>
          <button className="button primary" disabled={creating || !title.trim()}>
            {creating ? "正在创建…" : "创建并添加素材"}
          </button>
        </form>
      </section>

      <ErrorNotice error={error} onRetry={load} />

      <section className="section-block">
        <div className="section-heading">
          <div>
            <p className="eyebrow">YOUR WORK</p>
            <h2>Projects</h2>
          </div>
          <span className="quiet-count">{projects.length} 个</span>
        </div>
        {loading ? (
          <div className="loading-line">正在读取本地工作区…</div>
        ) : projects.length === 0 ? (
          <EmptyState title="还没有 Project">
            从右上方创建第一个主题。所有内容只保存在你的本地数据库。
          </EmptyState>
        ) : (
          <div className="project-grid">
            {projects.map((project) => (
              <Link className="project-card" to={`/projects/${project.id}`} key={project.id}>
                <div className="project-card-topline">
                  <span>{formatDate(project.updated_at)}</span>
                  <span aria-hidden="true">↗</span>
                </div>
                <h3>{project.title}</h3>
                <p>{project.description || "尚未添加说明。"}</p>
                <dl>
                  <div><dt>Sources</dt><dd>{project.source_count}</dd></div>
                  <div><dt>Runs</dt><dd>{project.run_count}</dd></div>
                </dl>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
