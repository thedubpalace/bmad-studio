import { ApiError } from "../api/client";
import { th } from "../i18n/th";

/** "mm:ss" สำหรับนับถอยหลังจาก Retry-After */
export function formatCountdown(totalSeconds: number): string {
  const safe = Math.max(0, Math.ceil(totalSeconds));
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export interface LoginFailure {
  message: string;
  /** วินาทีที่ต้องรอ (เฉพาะ 429) */
  retryAfter: number | null;
}

/** แปลง error ของ POST /api/login เป็นข้อความ UI (UX-DR3) */
export function describeLoginError(error: unknown): LoginFailure {
  if (!(error instanceof ApiError)) return { message: th.login.generic, retryAfter: null };
  if (error.status === 401) return { message: th.login.invalid, retryAfter: null };
  if (error.status === 429) {
    const fromDetails = (error.details as { retry_after?: unknown } | null)?.retry_after;
    const wait = error.retryAfter ?? (typeof fromDetails === "number" ? fromDetails : 60);
    return { message: th.login.rateLimited(formatCountdown(wait)), retryAfter: wait };
  }
  if (error.code === "audit_unavailable") return { message: th.login.auditUnavailable, retryAfter: null };
  if (error.code === "credentials_unavailable") return { message: th.login.credentialsUnavailable, retryAfter: null };
  if (error.status === 503) return { message: th.login.auditUnavailable, retryAfter: null };
  if (error.status === 0) return { message: th.errors.offline, retryAfter: null };
  if (error.status === 422) return { message: th.login.required, retryAfter: null };
  return { message: th.login.generic, retryAfter: null };
}
