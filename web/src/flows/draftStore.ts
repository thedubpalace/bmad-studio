// draft ของ flow editor ใน memory ต่อเป้าหมาย (ไม่ใช่ localStorage — design.md "กัน data loss")
// ไปหน้า DiffPreview แล้วกด "กลับไปแก้" ได้ draft เดิม; ไฟล์บนดิสก์เปลี่ยน (บันทึกแล้ว/แก้จากภายนอก) = ทิ้ง draft
import type { Draft } from "./flowDraft";

export interface StoredDraft {
  /** base_sha256 ของไฟล์ตอนเริ่มแก้ — ไม่ตรงกับที่โหลดใหม่ = draft นี้เก่าแล้ว */
  base: string | null;
  draft: Draft;
  acknowledged: string[];
}

const drafts = new Map<string, StoredDraft>();

export function loadDraft(key: string, base: string | null): StoredDraft | null {
  const stored = drafts.get(key);
  if (!stored) return null;
  if (stored.base !== base) {
    drafts.delete(key);
    return null;
  }
  return stored;
}

export function saveDraft(key: string, value: StoredDraft): void {
  drafts.set(key, value);
}

export function dropDraft(key: string): void {
  drafts.delete(key);
}

/** session จบ (logout/หมดอายุแล้ว login เป็นคนอื่น) — draft ไม่ข้าม session */
export function clearDrafts(): void {
  drafts.clear();
}
