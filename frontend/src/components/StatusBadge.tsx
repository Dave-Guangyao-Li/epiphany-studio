export function StatusBadge({ status }: { status: string }) {
  const normalized = status.replaceAll("_", " ");
  return <span className={`status-badge status-${status}`}>{normalized}</span>;
}
