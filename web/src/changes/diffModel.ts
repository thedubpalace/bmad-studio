// แปลง diff จาก server (AD-5: server คำนวณ, client เรนเดอร์เท่านั้น) เป็นแถวสำหรับแสดงผล
import type { Diff, DiffHunk, DiffLine } from "../api/types";

export const VIRTUALIZE_OVER = 2000;

export interface Segment {
  text: string;
  changed: boolean;
}

/** แบ่งข้อความตาม span ระดับคำ [start, end) ที่ server ส่งมา — span ผิดรูปถูกตัดทิ้ง.
 * span ของ server นับเป็น code point (Python str) จึงแปลงผ่าน Array.from ไม่ใช่ UTF-16 index */
export function segments(text: string, spans: number[][] | undefined): Segment[] {
  const chars = Array.from(text);
  const clean = (spans ?? [])
    .filter((s) => s.length === 2 && Number.isInteger(s[0]) && Number.isInteger(s[1]) && s[0] < s[1])
    .map(([a, b]) => [Math.max(0, a), Math.min(chars.length, b)] as const)
    .filter(([a, b]) => a < b)
    .sort((x, y) => x[0] - y[0]);
  const out: Segment[] = [];
  let pos = 0;
  for (const [a, b] of clean) {
    if (a < pos) continue; // ทับกัน
    if (a > pos) out.push({ text: chars.slice(pos, a).join(""), changed: false });
    out.push({ text: chars.slice(a, b).join(""), changed: true });
    pos = b;
  }
  if (pos < chars.length || out.length === 0) out.push({ text: chars.slice(pos).join(""), changed: false });
  return out;
}

/** ความกว้างคอลัมน์เลขบรรทัด (หน่วย ch) ตามเลขที่มากที่สุด */
export function numberWidth(diff: Diff): number {
  let max = 0;
  for (const hunk of diff.hunks) max = Math.max(max, hunk.old_start + hunk.old_lines, hunk.new_start + hunk.new_lines);
  return Math.max(3, String(max).length) + 1;
}

export type UnifiedRow =
  | { kind: "line"; line: DiffLine; key: string }
  | { kind: "fold"; count: number; key: string };

export interface SplitCell {
  line: DiffLine;
}

export type SplitRow =
  | { kind: "pair"; left: DiffLine | null; right: DiffLine | null; key: string }
  | { kind: "fold"; count: number; key: string };

/** จำนวนบรรทัดที่ไม่เปลี่ยนก่อน hunk แรก/ระหว่าง hunk (พับไว้) */
function gapBefore(hunks: DiffHunk[], index: number): number {
  const hunk = hunks[index];
  if (index === 0) return Math.max(0, hunk.old_start - 1);
  const prev = hunks[index - 1];
  return Math.max(0, hunk.old_start - (prev.old_start + prev.old_lines));
}

export function unifiedRows(diff: Diff): UnifiedRow[] {
  const rows: UnifiedRow[] = [];
  diff.hunks.forEach((hunk, h) => {
    const gap = gapBefore(diff.hunks, h);
    if (gap > 0) rows.push({ kind: "fold", count: gap, key: `f${h}` });
    hunk.lines.forEach((line, i) => rows.push({ kind: "line", line, key: `${h}:${i}` }));
  });
  return rows;
}

/** side-by-side: context อยู่ทั้งสองฝั่ง, ชุด del ต่อด้วย add จับคู่ทีละบรรทัด */
export function splitRows(diff: Diff): SplitRow[] {
  const rows: SplitRow[] = [];
  diff.hunks.forEach((hunk, h) => {
    const gap = gapBefore(diff.hunks, h);
    if (gap > 0) rows.push({ kind: "fold", count: gap, key: `f${h}` });
    let i = 0;
    const lines = hunk.lines;
    while (i < lines.length) {
      const line = lines[i];
      if (line.type === "context") {
        rows.push({ kind: "pair", left: line, right: line, key: `${h}:${i}` });
        i += 1;
        continue;
      }
      const dels: DiffLine[] = [];
      const adds: DiffLine[] = [];
      const start = i;
      while (i < lines.length && lines[i].type === "del") dels.push(lines[i++]);
      while (i < lines.length && lines[i].type === "add") adds.push(lines[i++]);
      const n = Math.max(dels.length, adds.length);
      if (n === 0) {
        // ชนิดบรรทัดที่ไม่รู้จัก (ข้อมูลผิดรูป) — แสดงสองฝั่งแล้วไปต่อ ไม่วนไม่จบ
        rows.push({ kind: "pair", left: line, right: line, key: `${h}:${i}` });
        i += 1;
        continue;
      }
      for (let k = 0; k < n; k += 1) {
        rows.push({ kind: "pair", left: dels[k] ?? null, right: adds[k] ?? null, key: `${h}:${start}:${k}` });
      }
    }
  });
  return rows;
}

export function lineCount(diff: Diff): number {
  return diff.hunks.reduce((sum, hunk) => sum + hunk.lines.length, 0);
}

/** เปลี่ยนเฉพาะ line ending/whitespace ทั้งไฟล์ — server ส่งเป็น errors[] */
export const WHITESPACE_CODES = new Set(["eol_only_change", "whitespace_only_change"]);

export interface Window {
  start: number;
  end: number;
}

/** ช่วงแถวที่ต้องเรนเดอร์จริงเมื่อ virtualize (แถวสูงคงที่) */
export function visibleWindow(scrollTop: number, viewport: number, rowHeight: number, total: number, overscan = 30): Window {
  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const last = Math.min(total, Math.ceil((scrollTop + viewport) / rowHeight) + overscan);
  return { start: first, end: Math.max(first, last) };
}
