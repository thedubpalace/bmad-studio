// HTTP client ตัวเดียวของ web: ใส่ X-CSRF-Token ให้ทุก mutation อัตโนมัติ และแปลง envelope (AD-12)
import { th } from "../i18n/th";
import type { ErrorEnvelope } from "./types";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export type AuthProblem = "expired" | "csrf";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly retryAfter: number | null;

  constructor(status: number, code: string, message: string, details: unknown = null, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.retryAfter = retryAfter;
  }
}

let csrfToken: string | null = null;
const listeners = new Set<(problem: AuthProblem, error: ApiError) => void>();

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

/** ฟังปัญหา session: 401 ระหว่างใช้งาน (expired) และ 403 csrf_invalid (csrf) */
export function onAuthProblem(listener: (problem: AuthProblem, error: ApiError) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function parseRetryAfter(value: string | null): number | null {
  if (!value) return null;
  const seconds = Number.parseInt(value, 10);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

export interface RequestOptions {
  /** ไม่แจ้ง listener เมื่อได้ 401 (ใช้กับ /api/login และ /api/session) */
  quietAuth?: boolean;
  signal?: AbortSignal;
}

export async function apiRequest<T>(method: string, path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
  const upper = method.toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (!SAFE_METHODS.has(upper) && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let response: Response;
  try {
    response = await fetch(path, {
      method: upper,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "network_error", th.errors.offline);
  }

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (response.ok && payload && typeof payload === "object" && (payload as { ok?: unknown }).ok === true) {
    return (payload as { data: T }).data;
  }

  const envelope = (payload ?? {}) as Partial<ErrorEnvelope>;
  const error = new ApiError(
    response.status,
    typeof envelope.code === "string" ? envelope.code : "http_error",
    typeof envelope.error === "string" && envelope.error ? envelope.error : th.errors.unexpected,
    envelope.details ?? null,
    parseRetryAfter(response.headers.get("Retry-After")),
  );
  if (!options.quietAuth) {
    if (error.status === 401) listeners.forEach((fn) => fn("expired", error));
    if (error.status === 403 && error.code === "csrf_invalid") listeners.forEach((fn) => fn("csrf", error));
  }
  throw error;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => apiRequest<T>("GET", path, undefined, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) => apiRequest<T>("POST", path, body ?? {}, options),
};
