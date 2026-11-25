// draft ของ flow editor — ฟังก์ชันล้วน (ไม่มี React) ทดสอบได้ตรง ๆ
// กฎ lock/validation ไม่อยู่ที่นี่ (AD-1): canMove ดูแค่ `locked` ที่ API ส่งมา และ server ตรวจ draft ทุกครั้ง
import type { EditorStage, EditorTrack, FlowEditor, FlowModel, FlowStage } from "../api/types";

export type Stage = Required<Pick<FlowStage, "id" | "agent" | "enabled" | "optional" | "after" | "gate" | "produces">> & {
  skill: string | null;
  condition: string | null;
};
export interface DraftTrack {
  entry_condition: { requirementsReady: boolean; status: string[] } | null;
  stages: Stage[];
}
export interface Draft {
  version: number;
  tracks: Record<string, DraftTrack>;
}
export type Meta = Pick<EditorStage, "locked" | "lockReason" | "mustFollowLocked" | "overridden" | "default">;

export const EDITABLE_FIELDS = ["enabled", "agent", "skill", "gate", "condition"] as const;
export type EditableField = (typeof EDITABLE_FIELDS)[number];

function toStage(s: FlowStage): Stage {
  return {
    id: s.id,
    agent: s.agent,
    skill: s.skill ?? null,
    enabled: s.enabled,
    optional: s.optional,
    after: [...s.after],
    gate: s.gate,
    produces: [...s.produces],
    condition: s.condition ?? null,
  };
}

function toTrack(t: { entry_condition?: DraftTrack["entry_condition"] | null; stages: FlowStage[] }): DraftTrack {
  const entry = t.entry_condition ?? null;
  return {
    entry_condition: entry ? { requirementsReady: entry.requirementsReady, status: [...entry.status] } : null,
    stages: t.stages.map(toStage),
  };
}

/** draft จาก flow เต็ม (defaultFlow / policyFix) หรือจาก tracks ของ view (ตัด metadata ของ editor ออก) */
export function draftFromFlow(flow: FlowModel | { version: number; tracks: Record<string, EditorTrack> }): Draft {
  const tracks: Record<string, DraftTrack> = {};
  for (const [name, track] of Object.entries(flow.tracks)) tracks[name] = toTrack(track);
  return { version: flow.version, tracks: sortTracks(tracks) };
}

export function draftFromView(view: FlowEditor): Draft {
  return draftFromFlow({ version: view.version, tracks: view.tracks });
}

function sortTracks(tracks: Record<string, DraftTrack>): Record<string, DraftTrack> {
  const out: Record<string, DraftTrack> = {};
  for (const [name, track] of Object.entries(tracks)) out[name] = { ...track, stages: inDisplayOrder(track.stages) };
  return out;
}

/** ลำดับแสดงผล = topological ตาม after, tie-break ตามตำแหน่งในรายการ (เหมือน resolver); มี cycle = ต่อท้ายตามรายการ */
export function displayOrder(stages: Stage[]): string[] {
  const index = new Map(stages.map((s, i) => [s.id, i]));
  const indegree = new Map<string, number>();
  const children = new Map<string, string[]>();
  for (const s of stages) {
    const deps = s.after.filter((d) => index.has(d));
    indegree.set(s.id, deps.length);
    for (const d of deps) children.set(d, [...(children.get(d) ?? []), s.id]);
  }
  const ready = stages.filter((s) => indegree.get(s.id) === 0).map((s) => s.id);
  const out: string[] = [];
  while (ready.length) {
    ready.sort((a, b) => (index.get(a) ?? 0) - (index.get(b) ?? 0));
    const id = ready.shift() as string;
    out.push(id);
    for (const child of children.get(id) ?? []) {
      const left = (indegree.get(child) ?? 0) - 1;
      indegree.set(child, left);
      if (left === 0) ready.push(child);
    }
  }
  for (const s of stages) if (!out.includes(s.id)) out.push(s.id);
  return out;
}

export function inDisplayOrder(stages: Stage[]): Stage[] {
  const byId = new Map(stages.map((s) => [s.id, s]));
  return displayOrder(stages).map((id) => byId.get(id) as Stage);
}

function uniq(items: string[]): string[] {
  return items.filter((item, i) => items.indexOf(item) === i);
}

/** สลับขั้น `id` ขึ้นเหนือขั้นก่อนหน้าในลำดับแสดงผล โดยแก้ `after` (ลำดับมาจาก after เท่านั้น — AD-7) */
function moveUp(stages: Stage[], id: string): Stage[] | null {
  const order = displayOrder(stages);
  const at = order.indexOf(id);
  if (at <= 0) return null;
  const prevId = order[at - 1];
  const byId = new Map(stages.map((s) => [s.id, s]));
  const x = byId.get(id) as Stage;
  const p = byId.get(prevId) as Stage;
  const next = stages.map((s) => {
    if (s.id === id) {
      // X รับตำแหน่งของ P: ตามหลังขั้นก่อนหน้าของ P (+ ขั้นก่อนหน้าอื่นของ X ที่ไม่ใช่ P)
      return { ...s, after: uniq([...p.after.filter((a) => a !== id), ...x.after.filter((a) => a !== prevId && a !== id)]) };
    }
    if (s.id === prevId) return { ...s, after: [id] };
    if (s.after.includes(id)) return { ...s, after: uniq(s.after.map((a) => (a === id ? prevId : a))) };
    return s;
  });
  return inDisplayOrder(next);
}

export type MoveBlock = "first" | "last" | "locked-self" | "locked-prev" | "locked-next" | null;

/** ย้ายได้ไหม — ดูเฉพาะ `locked` ของ API (ไม่สลับกับขั้นที่ล็อก ไม่ว่าทิศใด) */
export function moveBlock(stages: Stage[], meta: (id: string) => Meta | undefined, id: string, dir: -1 | 1): MoveBlock {
  const order = displayOrder(stages);
  const at = order.indexOf(id);
  if (meta(id)?.locked) return "locked-self";
  const other = order[at + dir];
  if (other === undefined) return dir < 0 ? "first" : "last";
  if (meta(other)?.locked) return dir < 0 ? "locked-prev" : "locked-next";
  return null;
}

export function moveStage(stages: Stage[], id: string, dir: -1 | 1): Stage[] | null {
  if (dir < 0) return moveUp(stages, id);
  const order = displayOrder(stages);
  const at = order.indexOf(id);
  const below = order[at + 1];
  return below === undefined ? null : moveUp(stages, below);
}

export function updateStage(draft: Draft, track: string, id: string, patch: Partial<Stage>): Draft {
  const t = draft.tracks[track];
  if (!t) return draft;
  return {
    ...draft,
    tracks: { ...draft.tracks, [track]: { ...t, stages: t.stages.map((s) => (s.id === id ? { ...s, ...patch } : s)) } },
  };
}

export function withStages(draft: Draft, track: string, stages: Stage[]): Draft {
  return { ...draft, tracks: { ...draft.tracks, [track]: { ...draft.tracks[track], stages } } };
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([k, v]) => [k, canonical(v)]),
    );
  }
  return value;
}

/** draft สองชุดเท่ากันไหม (ไม่สนลำดับในรายการของ stage — ลำดับมาจาก after) */
export function sameDraft(a: Draft, b: Draft): boolean {
  const norm = (d: Draft) =>
    canonical({
      version: d.version,
      tracks: Object.fromEntries(
        Object.entries(d.tracks).map(([n, t]) => [n, { ...t, stages: [...t.stages].sort((x, y) => (x.id < y.id ? -1 : 1)) }]),
      ),
    });
  return JSON.stringify(norm(a)) === JSON.stringify(norm(b));
}

export function fieldDiffers(stage: Stage, meta: Meta | undefined, field: EditableField): boolean {
  const base = meta?.default;
  if (!base) return false;
  return JSON.stringify(stage[field] ?? null) !== JSON.stringify((base as unknown as Record<string, unknown>)[field] ?? null);
}

export function defaultValue(meta: Meta | undefined, field: EditableField): unknown {
  return meta?.default ? (meta.default as unknown as Record<string, unknown>)[field] ?? null : undefined;
}

/** ขั้น enabled ถัดไปหลังขั้นที่ปิด (ข้อความ "ข้ามไปที่ …") */
export function nextEnabledAfter(stages: Stage[], id: string): string | null {
  const order = displayOrder(stages);
  const byId = new Map(stages.map((s) => [s.id, s]));
  for (const sid of order.slice(order.indexOf(id) + 1)) if (byId.get(sid)?.enabled) return sid;
  return null;
}
