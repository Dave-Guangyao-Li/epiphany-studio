import { ApiError } from "../api/client";

export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  const isApiError = error instanceof ApiError;
  const message = error instanceof Error ? error.message : "发生了未知错误";
  return (
    <section className="error-notice" role="alert">
      <div>
        <strong>请求没有完成</strong>
        <p>{message}</p>
        {isApiError && (
          <dl className="error-meta">
            <div><dt>HTTP</dt><dd>{error.status}</dd></div>
            <div><dt>Request ID</dt><dd><code>{error.requestId ?? "未返回"}</code></dd></div>
            <div><dt>请求</dt><dd><code>{error.method} {error.path}</code></dd></div>
          </dl>
        )}
      </div>
      {onRetry && <button className="button secondary" onClick={onRetry}>重试</button>}
    </section>
  );
}
