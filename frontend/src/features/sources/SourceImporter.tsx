import { type ChangeEvent, type FormEvent, useRef, useState } from "react";
import { projectsApi } from "../../api/epiphany";
import type { ImportSourceResponse, SourceType } from "../../api/types";
import { ErrorNotice } from "../../components/ErrorNotice";

export function SourceImporter({
  projectId,
  onImported,
}: {
  projectId: string;
  onImported: (result: ImportSourceResponse) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [sourceType, setSourceType] = useState<SourceType>("journal");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<unknown>(null);

  async function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setTitle(file.name.replace(/\.(md|markdown|txt)$/i, ""));
    setText(await file.text());
    setNotice(`已读取 ${file.name}，确认后才会导入。`);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || !text.trim()) return;
    setSubmitting(true);
    setError(null);
    setNotice("");
    try {
      const result = await projectsApi.importSource(projectId, {
        title: title.trim(),
        source_type: sourceType,
        text,
      });
      setNotice(
        result.created
          ? `已导入并切分为 ${result.source.segment_count} 个片段。`
          : result.linked
            ? "相同内容已经存在；已关联到当前 Project。"
            : "相同内容已经在当前 Project 中，没有创建副本。",
      );
      setTitle("");
      setText("");
      if (fileRef.current) fileRef.current.value = "";
      onImported(result);
    } catch (submitError) {
      setError(submitError);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="source-importer" onSubmit={submit}>
      <div className="form-row two-columns">
        <label>
          标题
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="这份素材是什么？"
            required
          />
        </label>
        <label>
          类型
          <select value={sourceType} onChange={(event) => setSourceType(event.target.value as SourceType)}>
            <option value="journal">日记 / 随想</option>
            <option value="voice_note_transcript">口述转写</option>
            <option value="podcast_draft">播客旧稿</option>
            <option value="writing_sample">写作样本</option>
            <option value="other">其他</option>
          </select>
        </label>
      </div>
      <label>
        正文
        <textarea
          className="source-textarea"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="直接粘贴日记、旧稿或已经转成文字的口述。这里不会直接接收音频。"
          rows={10}
          required
        />
      </label>
      <div className="form-actions split-actions">
        <div>
          <input
            ref={fileRef}
            className="visually-hidden"
            id="source-file"
            type="file"
            accept=".txt,.md,.markdown,text/plain,text/markdown"
            onChange={chooseFile}
          />
          <label className="button ghost" htmlFor="source-file">选择 TXT / Markdown</label>
        </div>
        <button className="button primary" disabled={submitting || !title.trim() || !text.trim()}>
          {submitting ? "正在导入…" : "导入 Source"}
        </button>
      </div>
      {notice && <p className="form-notice" role="status">{notice}</p>}
      <ErrorNotice error={error} />
    </form>
  );
}
