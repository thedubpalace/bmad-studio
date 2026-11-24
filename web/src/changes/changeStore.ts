// การเปลี่ยนแปลงที่รอตรวจ/ยืนยัน (ใน memory ของหน้าเท่านั้น — design.md "กัน data loss")
// editor ทุกตัวเรียก startChange() แล้วนำทางไป /changes/review?step=review
import { useSyncExternalStore } from "react";

import { api, ApiError } from "../api/client";
import type { ChangeCommit, ChangePreview, ChangeRequestBody } from "../api/types";

export type Outcome =
  | { kind: "idle" }
  | { kind: "writing" }
  | { kind: "done"; result: ChangeCommit }
  | { kind: "conflict"; error: ApiError }
  | { kind: "expired"; error: ApiError }
  /** ไม่รู้ว่าเขียนไปแล้วหรือยัง (เครือข่ายขาดหลังส่ง, 5xx ที่ไม่ใช่ 503) — ให้ไปดูที่ /changes */
  | { kind: "unknown"; error: ApiError }
  /** server ยืนยันว่าไม่มีอะไรเปลี่ยน (4xx, 503) — ลองใหม่ได้ */
  | { kind: "failed"; error: ApiError };

export interface PendingChange {
  request: ChangeRequestBody;
  preview: ChangePreview;
  /** หน้าที่จะกลับไปแก้ / โหลดใหม่ */
  returnTo: string;
  /** ข้อความที่ผู้ใช้แก้ (ให้คัดลอกได้เมื่อ conflict) */
  draft?: string;
  outcome: Outcome;
}

let pending: PendingChange | null = null;
const listeners = new Set<() => void>();

function emit(next: PendingChange | null): void {
  pending = next;
  listeners.forEach((fn) => fn());
}

export function getPending(): PendingChange | null {
  return pending;
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function usePendingChange(): PendingChange | null {
  return useSyncExternalStore(subscribe, getPending, getPending);
}

export function setPending(next: PendingChange | null): void {
  emit(next);
}

export function clearPending(): void {
  emit(null);
}

/** ยังมีงานที่ยังไม่เขียน (ใช้เตือนก่อนออกจากหน้า) */
export function hasUnsavedChange(): boolean {
  return pending !== null && pending.outcome.kind !== "done";
}

export async function startChange(request: ChangeRequestBody, returnTo: string, draft?: string): Promise<PendingChange> {
  const preview = await api.post<ChangePreview>("/api/changes/preview", request);
  const next: PendingChange = { request, preview, returnTo, draft, outcome: { kind: "idle" } };
  emit(next);
  return next;
}

/** เปิด preview ใหม่จาก request เดิม (หลัง token หมดอายุ) — ถ้า pending ถูกเปลี่ยนระหว่างรอ ผลนี้ถูกทิ้ง */
export async function refreshPreview(): Promise<void> {
  const current = pending;
  if (!current) return;
  const preview = await api.post<ChangePreview>("/api/changes/preview", current.request);
  if (pending !== current) return;
  emit({ ...current, preview, outcome: { kind: "idle" } });
}

let inflight: Promise<void> | null = null;

function classify(error: ApiError, previous: Outcome): Outcome["kind"] {
  if (error.status === 0) return "unknown";
  if (error.status === 409) {
    // ส่งซ้ำหลังเครือข่ายขาด: 409 อาจเป็นเพราะการเขียนครั้งก่อนของเราเอง
    return previous.kind === "unknown" ? "unknown" : "conflict";
  }
  if (error.code === "confirm_expired" || error.code === "confirm_invalid") return "expired";
  if (error.status >= 500 && error.status !== 503) return "unknown";
  return "failed";
}

/** ส่งคำสั่งเขียน — กดซ้ำระหว่างส่งได้ promise เดิม; server idempotent ต่อ preview_id อยู่แล้ว */
export function commitPending(typedConfirmation: string | null): Promise<void> {
  if (inflight) return inflight;
  const current = pending;
  if (!current || current.outcome.kind === "writing" || current.outcome.kind === "done") return Promise.resolve();
  const previous = current.outcome;
  const writing: PendingChange = { ...current, outcome: { kind: "writing" } };
  emit(writing);
  inflight = (async () => {
    try {
      const result = await api.post<ChangeCommit>("/api/changes/commit", {
        preview_id: current.preview.preview_id,
        confirm_token: current.preview.confirm_token,
        typed_confirmation: typedConfirmation,
      });
      if (pending === writing) emit({ ...current, outcome: { kind: "done", result } });
    } catch (cause) {
      const error = cause instanceof ApiError ? cause : new ApiError(0, "network_error", String(cause));
      if (pending === writing) emit({ ...current, outcome: { kind: classify(error, previous), error } as Outcome });
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}
