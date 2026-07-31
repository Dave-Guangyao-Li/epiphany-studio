const API_PREFIX = "/api";
const REQUEST_ID_HEADER = "x-request-id";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly requestId: string | null;
  readonly method: string;
  readonly path: string;

  constructor(args: {
    status: number;
    detail: string;
    requestId: string | null;
    method: string;
    path: string;
  }) {
    super(args.detail);
    this.name = "ApiError";
    this.status = args.status;
    this.detail = args.detail;
    this.requestId = args.requestId;
    this.method = args.method;
    this.path = args.path;
  }
}

function newRequestId(): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().replaceAll("-", "")
      : `${Date.now()}${Math.random().toString(16).slice(2)}`;
  return `req_ui_${suffix}`;
}

async function errorDetail(response: Response): Promise<string> {
  const fallback = `${response.status} ${response.statusText || "Request failed"}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail != null) return JSON.stringify(body.detail);
  } catch {
    // The backend can also return plain-text proxy errors.
  }
  return fallback;
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  if (options.body != null && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  if (!headers.has(REQUEST_ID_HEADER)) headers.set(REQUEST_ID_HEADER, newRequestId());

  const response = await fetch(`${API_PREFIX}${path}`, { ...options, headers });
  if (!response.ok) {
    throw new ApiError({
      status: response.status,
      detail: await errorDetail(response),
      requestId: response.headers.get(REQUEST_ID_HEADER),
      method,
      path,
    });
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function apiText(path: string): Promise<string> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    headers: { [REQUEST_ID_HEADER]: newRequestId() },
  });
  if (!response.ok) {
    throw new ApiError({
      status: response.status,
      detail: await errorDetail(response),
      requestId: response.headers.get(REQUEST_ID_HEADER),
      method: "GET",
      path,
    });
  }
  return response.text();
}

export async function apiOptional<T>(path: string): Promise<T | null> {
  try {
    return await apiRequest<T>(path);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 409)) {
      return null;
    }
    throw error;
  }
}

export function apiEventStreamUrl(path: string): string {
  return `${API_PREFIX}${path}`;
}
