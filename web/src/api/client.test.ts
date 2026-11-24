import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, onAuthProblem, setCsrfToken } from "./client";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error("expected the request to fail");
}

describe("api client", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("adds X-CSRF-Token to every mutation automatically", async () => {
    setCsrfToken("token-123");
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true, data: { done: true } }));
    await api.post("/api/anything", { a: 1 });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(init.headers["X-CSRF-Token"]).toBe("token-123");
    expect(init.credentials).toBe("same-origin");
  });

  it("does not send the CSRF token on GET", async () => {
    setCsrfToken("token-123");
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true, data: 1 }));
    await api.get("/api/session");
    expect(fetchMock.mock.calls[0][1].headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("unwraps the ok envelope", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true, data: { x: 1 } }));
    await expect(api.get("/api/x")).resolves.toEqual({ x: 1 });
  });

  it("turns error envelopes into ApiError with Retry-After", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(429, { ok: false, code: "rate_limited", error: "รอ" }, { "Retry-After": "42" }),
    );
    const error = await failure(api.post("/api/login", {}, { quietAuth: true }));
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(429);
    expect(error.code).toBe("rate_limited");
    expect(error.retryAfter).toBe(42);
  });

  it("reports 401 as an expired session and csrf_invalid as csrf problem", async () => {
    const seen: string[] = [];
    const off = onAuthProblem((problem) => seen.push(problem));
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { ok: false, code: "session_expired", error: "x" }));
    fetchMock.mockResolvedValueOnce(jsonResponse(403, { ok: false, code: "csrf_invalid", error: "x" }));
    fetchMock.mockResolvedValueOnce(jsonResponse(403, { ok: false, code: "forbidden", error: "x" }));
    await api.get("/api/a").catch(() => null);
    await api.post("/api/b").catch(() => null);
    await api.post("/api/c").catch(() => null);
    off();
    expect(seen).toEqual(["expired", "csrf"]);
  });

  it("stays quiet for login failures", async () => {
    const seen: string[] = [];
    const off = onAuthProblem((problem) => seen.push(problem));
    fetchMock.mockResolvedValue(jsonResponse(401, { ok: false, code: "invalid_credentials", error: "x" }));
    await api.post("/api/login", {}, { quietAuth: true }).catch(() => null);
    off();
    expect(seen).toEqual([]);
  });

  it("maps network failures to a Thai offline error", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await failure(api.get("/api/x"));
    expect(error.code).toBe("network_error");
    expect(error.message).toContain("เชื่อมต่อไม่ได้");
  });
});
