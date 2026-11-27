// draft ของ ConfigEditor ใน memory ต่อชั้น (team/user) — กลับจากหน้า review แล้วได้ draft เดิม
// ไฟล์เปลี่ยน (sha ไม่ตรง) = ทิ้ง; session เปลี่ยนผู้ใช้/logout = ล้าง (SessionContext)
export type Layer = "team" | "user";
export type Mode = "form" | "text";
export type Scalar = string | number | boolean;

/** ค่าในฟอร์ม: ตัวเลขเก็บเป็นข้อความดิบจนกว่าจะส่ง (พิมพ์ "1." ระหว่างทางได้) */
export type Edit =
  | { kind: "set"; path: string[]; value: Scalar; numeric?: boolean }
  | { kind: "unset"; path: string[] };

export interface ConfigDraft {
  base: string | null;
  mode: Mode;
  /** key = ConfigRow.id (JSON ของ path — ไม่ชนกันแม้ชื่อ key มีจุด) */
  edits: Record<string, Edit>;
  text: string | null;
}

const drafts = new Map<Layer, ConfigDraft>();

export function loadConfigDraft(layer: Layer, base: string | null): ConfigDraft | null {
  const stored = drafts.get(layer);
  if (!stored) return null;
  if (stored.base !== base) {
    drafts.delete(layer);
    return null;
  }
  return stored;
}

export function saveConfigDraft(layer: Layer, draft: ConfigDraft): void {
  const dirty = Object.keys(draft.edits).length > 0 || draft.text !== null;
  if (dirty) drafts.set(layer, draft);
  else drafts.delete(layer);
}

export function clearConfigDrafts(): void {
  drafts.clear();
}
