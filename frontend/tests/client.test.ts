import { afterEach, describe, expect, it, vi } from "vitest";
import { apiRequest } from "../src/api/client";

afterEach(() => vi.restoreAllMocks());

describe("API client", () => {
  it("preserves the backend request ID in a recoverable error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "source not found" }),
      {
        status: 404,
        headers: {
          "content-type": "application/json",
          "x-request-id": "req_backend_123",
        },
      },
    )));

    const promise = apiRequest("/sources/src_missing");
    await expect(promise).rejects.toMatchObject({
      status: 404,
      requestId: "req_backend_123",
      method: "GET",
      path: "/sources/src_missing",
    });
  });

  it("sends a UI request ID for correlation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    await apiRequest("/health");
    const options = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(options.headers).get("x-request-id")).toMatch(/^req_ui_/);
  });
});
