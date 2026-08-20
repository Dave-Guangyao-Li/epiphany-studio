import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { runsApi } from "../../api/epiphany";
import type { ArtifactView, InterviewScaffold, RunView } from "../../api/types";
import { ErrorNotice } from "../../components/ErrorNotice";

const SCAFFOLD_KIND = "build_interview_scaffold_result";
const SCAFFOLD_EDIT_KIND = "interview_scaffold_human_edit";

function stableId(prefix: string) {
  return `${prefix}-${crypto.randomUUID().replaceAll("-", "")}`;
}

function latestArtifact(artifacts: ArtifactView[], kind: string): ArtifactView | null {
  const matching = artifacts
    .filter((artifact) => artifact.kind === kind)
    .sort((left, right) => {
      if (left.created_at !== right.created_at) {
        return left.created_at < right.created_at ? 1 : -1;
      }
      return left.id < right.id ? 1 : -1;
    });
  return matching[0] ?? null;
}

function cloneScaffold(scaffold: InterviewScaffold): InterviewScaffold {
  return JSON.parse(JSON.stringify(scaffold)) as InterviewScaffold;
}

/**
 * Lets a person edit the human-facing text of an Interview Scaffold. Saving
 * creates a new, immutable "human-edited" Artifact (Plan A / ADR-0002): the
 * original AI scaffold is never overwritten, citations and structure are
 * preserved by the backend, and the exported scaffold uses the latest edit.
 */
export function ScaffoldEditorPanel({
  run,
  onSaved,
}: {
  run: RunView;
  onSaved?: () => void | Promise<void>;
}) {
  const base = useMemo(() => latestArtifact(run.artifacts, SCAFFOLD_KIND), [run.artifacts]);
  const latestEdit = useMemo(
    () => latestArtifact(run.artifacts, SCAFFOLD_EDIT_KIND),
    [run.artifacts],
  );

  const effective = useMemo<InterviewScaffold | null>(() => {
    if (latestEdit) {
      const edited = (latestEdit.content_json as { scaffold?: InterviewScaffold }).scaffold;
      if (edited) return edited;
    }
    if (base) {
      const content = { ...(base.content_json as Record<string, unknown>) };
      delete content._execution;
      return content as unknown as InterviewScaffold;
    }
    return null;
  }, [base, latestEdit]);

  const effectiveArtifactId = latestEdit?.id ?? base?.id ?? null;
  const [draft, setDraft] = useState<InterviewScaffold | null>(
    effective ? cloneScaffold(effective) : null,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const retryRef = useRef<{ fingerprint: string; id: string } | null>(null);

  // Reset the form only when the effective version changes (a different Run or a
  // newly saved edit), never on every keystroke.
  useEffect(() => {
    setDraft(effective ? cloneScaffold(effective) : null);
    setNotice("");
    retryRef.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveArtifactId]);

  if (!base || !draft) return null;

  function updateDraft(mutate: (next: InterviewScaffold) => void) {
    setDraft((current) => {
      if (!current) return current;
      const next = cloneScaffold(current);
      mutate(next);
      return next;
    });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!base || !draft) return;
    const fingerprint = JSON.stringify(draft);
    if (retryRef.current?.fingerprint !== fingerprint) {
      retryRef.current = { fingerprint, id: stableId("ui-scaffold-edit") };
    }
    setSaving(true);
    setError(null);
    setNotice("");
    try {
      await runsApi.saveScaffoldEdit(run.id, {
        submission_id: retryRef.current.id,
        base_artifact_id: base.id,
        scaffold: draft,
      });
      setNotice("已保存为人工编辑版。原始 AI 脚手架保持不变，导出会使用最新人工版。");
      await onSaved?.();
    } catch (saveError) {
      setError(saveError);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="action-card scaffold-editor">
      <p className="eyebrow">EDIT SCAFFOLD</p>
      <h3>
        编辑采访脚手架
        {latestEdit && <span className="scaffold-edited-badge"> · 已编辑（人工版）</span>}
      </h3>
      <p>
        只可修改面向人的文字；来源引用、结构和标题保持不变。保存会生成一个新的人工编辑版，
        不覆盖原始 AI 版本，导出使用最新人工版。
      </p>
      <form onSubmit={save}>
        <label>
          标题（与主题绑定，不可修改）
          <input value={draft.title} readOnly disabled />
        </label>
        <label>
          本期意图
          <textarea
            rows={2}
            value={draft.episode_intent.text}
            onChange={(event) =>
              updateDraft((next) => {
                next.episode_intent.text = event.target.value;
              })
            }
          />
        </label>
        <label>
          开场白
          <textarea
            rows={3}
            value={draft.opening.text}
            onChange={(event) =>
              updateDraft((next) => {
                next.opening.text = event.target.value;
              })
            }
          />
        </label>
        {draft.sections.map((section, sectionIndex) => (
          <fieldset key={sectionIndex}>
            <legend>小节 {sectionIndex + 1}</legend>
            <label>
              小节标题
              <input
                value={section.title}
                onChange={(event) =>
                  updateDraft((next) => {
                    next.sections[sectionIndex].title = event.target.value;
                  })
                }
              />
            </label>
            <label>
              过渡语
              <textarea
                rows={2}
                value={section.transition.text}
                onChange={(event) =>
                  updateDraft((next) => {
                    next.sections[sectionIndex].transition.text = event.target.value;
                  })
                }
              />
            </label>
            {section.questions.map((question, questionIndex) => (
              <div className="scaffold-question-edit" key={questionIndex}>
                <label>
                  {`小节 ${sectionIndex + 1} · 问题 ${questionIndex + 1}`}
                  <textarea
                    rows={2}
                    value={question.prompt}
                    onChange={(event) =>
                      updateDraft((next) => {
                        next.sections[sectionIndex].questions[questionIndex].prompt =
                          event.target.value;
                      })
                    }
                  />
                </label>
                <label>
                  {`小节 ${sectionIndex + 1} · 问题 ${questionIndex + 1} · 追问目的`}
                  <textarea
                    rows={2}
                    value={question.purpose}
                    onChange={(event) =>
                      updateDraft((next) => {
                        next.sections[sectionIndex].questions[questionIndex].purpose =
                          event.target.value;
                      })
                    }
                  />
                </label>
              </div>
            ))}
          </fieldset>
        ))}
        <label>
          收尾
          <textarea
            rows={3}
            value={draft.closing.text}
            onChange={(event) =>
              updateDraft((next) => {
                next.closing.text = event.target.value;
              })
            }
          />
        </label>
        <button className="button primary" disabled={saving}>
          {saving ? "正在保存…" : "保存人工编辑版"}
        </button>
      </form>
      {notice && <p className="form-notice" role="status">{notice}</p>}
      <ErrorNotice error={error} />
    </section>
  );
}
